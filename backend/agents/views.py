"""
에이전트 API — 계정 보호 + 대화 영속.

- AdviseView : 베이스라인 → 1회 종합 액션 (자기 업체만)
- ChatView   : 세 에이전트와 대화. 사용자/에이전트 메시지를 DB에 저장(세션 지속).
- HistoryView: 업체 대화 이력 조회(새 기기/재접속에도 복원).
"""
import threading

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from onboarding.models import ChatMessage, DeepReport, Merchant

from .chat import run_chat
from .deepreport import run_report
from .orchestrator import run_geo


def _merchant(request, merchant_id):
    """소유자 스코프 — 남의 업체 접근 차단."""
    return get_object_or_404(Merchant, pk=merchant_id, owner=request.user)


class AdviseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        return Response(run_geo(merchant, request.data.get("question", "")))


class ChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        message = (request.data.get("message") or "").strip()
        forced = request.data.get("persona") or None
        if not message:
            return Response({"detail": "메시지가 비어 있습니다."}, status=400)

        # 저장된 이력을 LLM 맥락으로(시스템 메시지는 제외)
        history = [
            {"role": m.role, "text": m.text}
            for m in merchant.messages.exclude(role=ChatMessage.Role.SYSTEM)
        ]
        result = run_chat(merchant, history, message, forced)

        # 영속: 사용자 메시지 + (갱신 시)시스템 + 에이전트 답변들
        ChatMessage.objects.create(merchant=merchant, role="user", text=message)
        if result.get("updated_fields"):
            ChatMessage.objects.create(
                merchant=merchant, role="system",
                text="베이스라인 갱신: " + ", ".join(
                    f"{k} {v}" for k, v in result["updated_fields"].items()),
            )
        for t in result["turns"]:
            ChatMessage.objects.create(
                merchant=merchant, role="agent", persona=t["persona"], text=t["reply"])

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
        "credits_charged": r.credits_charged, "meta": r.meta, "error": r.error,
        "created_at": r.created_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


class ReportView(APIView):
    """P2.5 딥리포트 — POST 생성(백그라운드 스레드 실행), GET 폴링."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
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
