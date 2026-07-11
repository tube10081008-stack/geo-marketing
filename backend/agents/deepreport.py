"""
P2.5 복어(Fugu) 딥리포트 파이프라인 (Marlin-lite, RESEARCH-treequest-marlin.md §2.2).

구조: 컨텍스트 수집 → 섹션 생성(딥 티어는 '이달의 포커스'만 AB-MCTS 탐색) → 합성(MD).
- 원가 통제: 저가 provider 재사용, 탐색은 포커스 1개 섹션·소예산에만.
- judge(§1.4): LLM-judge는 사전 필터 — 최종 심판은 실측 A/B(근거등급 격상 루프).
- Stub에서도 결정적으로 동작(무키 개발·테스트), Stub은 0크레딧.
- 실행 주체: views의 백그라운드 스레드(월 1회/가게 규모엔 워커 불요).
"""
from __future__ import annotations

import re

from django.utils import timezone

from .chat import _card_summary
from .corpus import retrieve
from .llm import Meter, get_provider
from .personas import PERSONA_META

# 딥 티어의 AB-MCTS 예산(생성+채점 호출쌍 수). 원가 ≈ budget × $0.001
SEARCH_BUDGET = 8

SECTIONS = [
    ("summary", "요약", None),
    ("focus", "이달의 포커스", None),        # 딥 티어 탐색 대상
    ("acq", "획득 플랜", "acq"),
    ("cvr", "전환 플랜", "cvr"),
    ("ret", "유지 플랜", "ret"),
    ("measure", "측정 체크리스트", None),
]


def _context(merchant):
    card = merchant.baseline_card()
    recent = list(merchant.messages.order_by("-created_at")[:6])
    chat_note = " / ".join(f"{m.role}:{m.text[:60]}" for m in reversed(recent)) or "(대화 없음)"
    cites = "\n".join(
        f"- [{e.mid}] {e.claim} ({e.cite})"
        for p in ("acq", "cvr", "ret") for e in retrieve(p)
    )
    return card, (
        f"[가게] {merchant.name} / 업종 {merchant.industry} / {merchant.location}\n"
        f"[베이스라인] {_card_summary(card)} (종합등급 {card['overall_data_grade']})\n"
        f"[최근 대화] {chat_note}\n[코퍼스]\n{cites}"
    )


def _judge(provider, meter, text: str) -> float:
    """루브릭 채점(0~1): 측정목표·[M#] 인용·실행 구체성. Stub이면 결정적 휴리스틱."""
    if provider.name == "stub":
        s = 0.3
        s += 0.3 if re.search(r"\d", text) else 0          # 수치 목표
        s += 0.3 if "[M" in text else 0                     # 근거 인용
        s += min(0.1, len(text) / 8000)                     # 구체성(상한)
        return round(min(1.0, s), 3)
    out, usage = provider.complete(
        model=provider.router_model,
        system=("마케팅 액션 채점기. 루브릭: ①측정가능한 목표 ②[M#] 근거 인용 정합 "
                "③베이스라인 적합 ④실행 구체성. 0~1 사이 숫자 하나만 출력."),
        prompt=text[:3000], max_tokens=8)
    meter.add(usage)
    m = re.search(r"0?\.\d+|[01]", out)
    return min(1.0, float(m.group())) if m else 0.5


def _gen_section(provider, meter, title, persona, ctx, refine_from=None):
    if persona:
        label = f"{PERSONA_META[persona]['emoji']} {PERSONA_META[persona]['name']}"
        role = f"'{label}' 담당 에이전트"
    else:
        role = "마케팅 총괄 🐡 복어(Fugu)"
    system = (f"너는 소상공인 리포트의 '{title}' 섹션을 쓰는 {role}다. "
              "Markdown, 6~12줄, 측정가능한 목표와 [M#] 인용 필수. 출처 없는 단정 금지. "
              "수치 근거는 원 연구 맥락(주로 해외)임을 유지하고 실측 A/B로 검증한다고 명시.")
    prompt = ctx + (f"\n\n[개선 대상 초안]\n{refine_from}\n→ 위 초안을 더 구체적으로 정제하라."
                    if refine_from else f"\n\n'{title}' 섹션을 작성하라.")
    text, usage = provider.complete(model=provider.generator_model,
                                    system=system, prompt=prompt, max_tokens=700)
    meter.add(usage)
    return text or f"({title} 생성 실패 — 재시도 필요)"


def _search_focus(provider, meter, ctx, log: dict):
    """딥 티어: '이달의 포커스'를 AB-MCTS로 탐색(treequest 설치 시), 아니면 best-of-3."""
    def generate(parent):
        text = _gen_section(provider, meter, "이달의 포커스", None, ctx,
                            refine_from=parent["text"] if parent else None)
        return {"text": text}, _judge(provider, meter, text)

    try:
        import treequest as tq
        algo = tq.ABMCTSA()
        tree = algo.init_tree()
        for _ in range(SEARCH_BUDGET):
            tree = algo.step(tree, {"focus": generate})
        best, score = tq.top_k(tree, algo, k=1)[0]
        log["focus_search"] = {"engine": "ab-mcts", "budget": SEARCH_BUDGET, "best_score": score}
        return best["text"]
    except ImportError:
        cands = [generate(None) for _ in range(3)]
        best, score = max(cands, key=lambda c: c[1])
        log["focus_search"] = {"engine": "best-of-3", "best_score": score}
        return best["text"]


def run_report(report_id: int):
    """리포트 잡 실행(스레드 안전 — 자체 커넥션 사용). views에서 스레드로 호출."""
    from onboarding.models import DeepReport

    report = DeepReport.objects.select_related("merchant").get(pk=report_id)
    report.status = DeepReport.Status.RUNNING
    report.save(update_fields=["status"])
    try:
        provider = get_provider()
        meter = Meter()
        card, ctx = _context(report.merchant)
        log: dict = {}

        parts = [f"# {report.merchant.name} — 🐡 Fugu 딥리포트",
                 f"_{timezone.now():%Y-%m-%d} · 데이터등급 {card['overall_data_grade']} · 티어 {report.tier}_"]
        for key, title, persona in SECTIONS:
            if key == "focus" and report.tier == DeepReport.Tier.DEEP:
                body = _search_focus(provider, meter, ctx, log)
            else:
                body = _gen_section(provider, meter, title, persona, ctx)
            parts.append(f"\n## {title}\n{body}")
        parts.append("\n---\n_근거 수치는 원 연구 맥락 기준의 방향성 지침이며, "
                     "효과는 이 가게의 실측 A/B로 검증합니다. 데모(stub) 생성분은 무과금._")

        report.content_md = "\n".join(parts)
        # 포인트 과금: 실사용 원가를 소유 계정 지갑에서 차감(Stub은 무과금)
        points = 0
        if provider.name != "stub" and report.merchant.owner_id:
            from accounts.models import charge
            points, _ = charge(report.merchant.owner, meter.total_cost_usd())
        report.credits_charged = points  # 필드명 유지, 값은 포인트
        report.meta = {"provider": provider.name, "calls": len(meter.calls),
                       "raw_cost_usd": round(meter.total_cost_usd(), 6),
                       "points_charged": points, **log}
        report.status = DeepReport.Status.DONE
    except Exception as exc:  # noqa: BLE001 — 잡 실패는 상태로 보고
        report.status = DeepReport.Status.FAILED
        report.error = str(exc)[:1000]
    report.finished_at = timezone.now()
    report.save()
