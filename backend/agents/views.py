"""
에이전트 API — 계정 보호 + 대화 영속 + 포인트 과금(젠스파크식).

- AdviseView : 베이스라인 → 1회 종합 액션 (자기 업체만)
- ChatView   : 세 에이전트와 대화. 사용자/에이전트 메시지를 DB에 저장(세션 지속).
- HistoryView: 업체 대화 이력 조회(새 기기/재접속에도 복원).

과금: '대화당 1크레딧'이 아니라 호출별 실사용 토큰 원가를 포인트로 환산해
Wallet에서 차감(accounts.models.charge). Stub(데모)은 무과금, 잔액 0 이하는 402.
"""
import threading

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import charge, get_wallet
from onboarding.models import (
    Action, ChatMessage, DeepReport, Merchant, record_snapshot,
)

from .chat import MAX_HISTORY_MESSAGES, maybe_compact, run_chat
from .deepreport import run_report
from .llm import Meter, get_provider
from .orchestrator import run_geo


def _billing_gate(user, provider):
    """잔액 0 이하 + 실과금 프로바이더면 402 응답을, 아니면 None을 돌려준다."""
    if provider.name != "stub" and get_wallet(user).balance <= 0:
        return Response(
            {"detail": "포인트가 모두 소진되었습니다. 충전 후 이용해 주세요.",
             "balance": get_wallet(user).balance},
            status=402)
    return None


def _charge(user, provider, meter):
    """실사용 원가를 포인트로 차감. 반환: billing dict 조각."""
    cost = meter.total_cost_usd()
    if provider.name == "stub":
        return {"points_charged": 0, "balance": get_wallet(user).balance,
                "raw_cost_usd": round(cost, 6), "note": "데모(stub)는 무과금"}
    points, balance = charge(user, cost)
    return {"points_charged": points, "balance": balance,
            "raw_cost_usd": round(cost, 6)}


def _merchant(request, merchant_id):
    """소유자 스코프 — 남의 업체 접근 차단."""
    return get_object_or_404(Merchant, pk=merchant_id, owner=request.user)


# 대화 추출 지표 → 베이스라인 모델 매핑(서버가 진실의 원천)
_UPDATE_MAP = {
    "avg_rating": ("acquisition", "avg_rating"),
    "review_count": ("acquisition", "review_count"),
    "aov": ("conversion", "aov"),
    "monthly_customers": ("conversion", "monthly_customers"),
    "visit_to_purchase_rate": ("conversion", "visit_to_purchase_rate"),
    "revisit_rate": ("retention", "revisit_rate"),
    "regular_ratio": ("retention", "regular_ratio"),
    "nps": ("retention", "nps"),
}


def _apply_updates(merchant, updates: dict):
    """대화 중 갱신된 지표를 DB에 영속 + 시계열 스냅샷 축적(전/후 검증의 원천)."""
    for key, value in updates.items():
        record_snapshot(merchant, key, value, source="chat")
        target = _UPDATE_MAP.get(key)
        if not target:
            continue
        rel, field = target
        base = getattr(merchant, rel, None)
        if base is not None:
            setattr(base, field, value)
            base.save(update_fields=[field])


class AdviseView(APIView):
    permission_classes = [IsAuthenticated]

    # 페르소나별 기본 추적 지표 — 사장님이 실제로 잴 수 있는 것 우선(공개/장부 지표)
    _DEFAULT_TARGET = {"acq": "review_count", "cvr": "aov", "ret": "revisit_rate"}

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        provider = get_provider()
        gate = _billing_gate(request.user, provider)
        if gate:
            return gate
        meter = Meter()
        result = run_geo(merchant, request.data.get("question", ""),
                         provider=provider, meter=meter)

        # 조언 → 추적 가능한 액션 티켓(페르소나당 미결 1건만 유지, 중복 제안 방지)
        open_personas = set(
            merchant.actions.filter(status__in=["proposed", "active"])
            .values_list("persona", flat=True))
        for a in result.get("actions", []):
            if a["persona"] in open_personas:
                a["action_id"] = None
                continue
            ticket = Action.objects.create(
                merchant=merchant, persona=a["persona"],
                title=(a.get("action") or "")[:300], detail=a.get("action") or "",
                target_metric=self._DEFAULT_TARGET.get(a["persona"], ""),
                citations=a.get("citations") or [])
            a["action_id"] = ticket.pk

        result["billing"].update(_charge(request.user, provider, meter))
        return Response(result)


class ChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        message = (request.data.get("message") or "").strip()
        forced = request.data.get("persona") or None
        if not message:
            return Response({"detail": "메시지가 비어 있습니다."}, status=400)

        provider = get_provider()
        gate = _billing_gate(request.user, provider)
        if gate:
            return gate
        meter = Meter()

        # 최근 창만 원문으로 로드(그 이전은 merchant.chat_summary가 대신한다)
        recent = list(
            merchant.messages.exclude(role=ChatMessage.Role.SYSTEM)
            .order_by("-pk")[:MAX_HISTORY_MESSAGES])[::-1]
        history = [{"role": m.role, "text": m.text} for m in recent]

        # 최신 완료 딥리포트 발췌 — 에이전트가 실제 리포트를 참조·인용할 수 있게
        report_note = ""
        last_report = merchant.reports.filter(status=DeepReport.Status.DONE).first()
        if last_report:
            when = (last_report.finished_at.strftime("%Y-%m-%d")
                    if last_report.finished_at else "")
            report_note = (f"리포트 #{last_report.pk} · {when} · 티어 {last_report.tier}\n"
                           + last_report.content_md[:700])

        # 진행 중 실행안 — 에이전트가 자연스럽게 진행 상황·지표를 확인하도록 주입
        open_actions = merchant.actions.filter(status__in=["proposed", "active"])[:5]
        actions_note = "\n".join(
            f"- [{a.persona}] {a.title[:80]} (상태: {a.get_status_display()}"
            + (f", 타깃 {a.target_metric}" if a.target_metric else "") + ")"
            for a in open_actions)

        result = run_chat(merchant, history, message, forced,
                          provider=provider, meter=meter, report_note=report_note,
                          actions_note=actions_note)

        # 영속: 사용자 메시지 + (갱신 시)베이스라인 DB 반영 + 시스템 + 에이전트 답변들
        ChatMessage.objects.create(merchant=merchant, role="user", text=message)
        if result.get("updated_fields"):
            _apply_updates(merchant, result["updated_fields"])
            ChatMessage.objects.create(
                merchant=merchant, role="system",
                text="베이스라인 갱신: " + ", ".join(
                    f"{k} {v}" for k, v in result["updated_fields"].items()),
            )
        for t in result["turns"]:
            ChatMessage.objects.create(
                merchant=merchant, role="agent", persona=t["persona"], text=t["reply"])

        # 장기기억 컴팩션(필요 시 저가 모델 1콜) → 그 비용까지 합산해 포인트 차감
        compacted = maybe_compact(merchant, provider, meter)
        result["billing"].update(_charge(request.user, provider, meter))
        result["billing"]["memory_compacted"] = compacted

        return Response(result)


class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, merchant_id):
        merchant = _merchant(request, merchant_id)
        msgs = [
            {"role": m.role, "persona": m.persona, "text": m.text,
             "created_at": m.created_at.isoformat()}
            for m in merchant.messages.all()
        ]
        return Response({"merchant": merchant.name, "messages": msgs})


def _report_payload(r: DeepReport) -> dict:
    return {
        "id": r.pk, "tier": r.tier, "status": r.status,
        "content_md": r.content_md if r.status == DeepReport.Status.DONE else "",
        "points_charged": r.credits_charged, "meta": r.meta, "error": r.error,
        "created_at": r.created_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


class ReportView(APIView):
    """P2.5 딥리포트 — POST 생성(백그라운드 스레드 실행), GET 폴링."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        gate = _billing_gate(request.user, get_provider())
        if gate:
            return gate
        tier = request.data.get("tier", DeepReport.Tier.LITE)
        if tier not in DeepReport.Tier.values:
            return Response({"detail": "tier는 lite|deep"}, status=400)
        # 동시 중복 생성 방지(업체당 진행 중 1건)
        if merchant.reports.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "이미 생성 중인 리포트가 있습니다."}, status=409)
        report = DeepReport.objects.create(merchant=merchant, tier=tier)
        threading.Thread(target=run_report, args=(report.pk,), daemon=True).start()
        return Response(_report_payload(report), status=202)

    def get(self, request):
        merchant = _merchant(request, request.query_params.get("merchant_id"))
        return Response([_report_payload(r) for r in merchant.reports.all()[:10]])


class ReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, report_id):
        report = get_object_or_404(
            DeepReport, pk=report_id, merchant__owner=request.user)
        return Response(_report_payload(report))


class CheckinView(APIView):
    """주간 체크인 — 30초 측정 수집기(루프 ③). 마지막 측정 7일 경과 시 due.

    묻는 지표는 사장님이 즉시 아는 것만: 평점·리뷰수(플레이스 화면), 월매출(장부).
    """

    permission_classes = [IsAuthenticated]
    FIELDS = ("avg_rating", "review_count", "monthly_revenue")
    DUE_DAYS = 7

    def get(self, request):
        from django.utils import timezone

        merchant = _merchant(request, request.query_params.get("merchant_id"))
        last = merchant.snapshots.last()
        days = (timezone.now() - last.created_at).days if last else None
        return Response({
            "due": last is None or days >= self.DUE_DAYS,
            "days_since": days,
            "last_at": last.created_at.isoformat() if last else None,
        })

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        recorded = {}
        for f in self.FIELDS:
            v = request.data.get(f)
            if v in (None, ""):
                continue
            if record_snapshot(merchant, f, v, source="checkin") is None:
                continue  # 숫자 아님 → 무시
            recorded[f] = v
        if not recorded:
            return Response({"detail": "기록할 숫자가 없습니다."}, status=400)

        # 베이스라인(현재값)도 동기화
        _apply_updates(merchant, {k: str(v) for k, v in recorded.items()
                                  if k in _UPDATE_MAP})
        if "monthly_revenue" in recorded:
            try:
                merchant.monthly_revenue = int(
                    str(recorded["monthly_revenue"]).replace(",", ""))
                merchant.save(update_fields=["monthly_revenue"])
            except ValueError:
                pass
        ChatMessage.objects.create(
            merchant=merchant, role="system",
            text="주간 체크인 기록: " + ", ".join(f"{k} {v}" for k, v in recorded.items()))
        return Response({"recorded": recorded})


def _action_payload(a: Action) -> dict:
    return {
        "id": a.pk, "persona": a.persona, "title": a.title,
        "target_metric": a.target_metric, "citations": a.citations,
        "status": a.status,
        "baseline_value": float(a.baseline_value) if a.baseline_value is not None else None,
        "result_value": float(a.result_value) if a.result_value is not None else None,
        "delta_pct": a.delta(),
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "created_at": a.created_at.isoformat(),
    }


class ActionsView(APIView):
    """액션 티켓 목록 — 조언→실측 루프의 현재 상태판."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        merchant = _merchant(request, request.query_params.get("merchant_id"))
        return Response([_action_payload(a) for a in merchant.actions.all()[:20]])


class ActionDetailView(APIView):
    """상태 전이: 시작(baseline 캡처) → 완료(실측 캡처, 델타 산출) / 중단."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, action_id):
        from django.utils import timezone

        act = get_object_or_404(Action, pk=action_id, merchant__owner=request.user)
        to = request.data.get("status")
        if to not in ("active", "done", "dropped"):
            return Response({"detail": "status는 active|done|dropped"}, status=400)

        def latest(metric, after=None):
            qs = act.merchant.snapshots.filter(metric=metric)
            if after:
                qs = qs.filter(created_at__gt=after)
            snap = qs.last()
            return snap.value if snap else None

        note = ""
        if to == "active" and act.status == Action.Status.PROPOSED:
            act.started_at = timezone.now()
            if act.target_metric:
                act.baseline_value = latest(act.target_metric)
                if act.baseline_value is None:
                    note = "시작 시점 값이 없어요 — 채팅에 현재 값을 알려주면 기준선으로 잡습니다."
        elif to == "done" and act.status == Action.Status.ACTIVE:
            act.closed_at = timezone.now()
            if act.target_metric:
                act.result_value = latest(act.target_metric, after=act.started_at)
                if act.result_value is None:
                    note = ("시작 이후 측정값이 없어 델타를 못 냈어요 — 채팅에 "
                            "현재 값을 말한 뒤 다시 완료 처리하면 실측이 기록됩니다.")
                    act.closed_at = None
                    act.save()
                    return Response(
                        {**_action_payload(act), "note": note, "detail": note}, status=409)
        elif to == "dropped":
            act.closed_at = timezone.now()
        else:
            return Response({"detail": f"{act.status}→{to} 전이는 허용되지 않습니다."}, status=409)
        act.status = to
        act.save()
        return Response({**_action_payload(act), "note": note})
