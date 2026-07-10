"""
지오 오케스트레이션 — 라우팅 → 페르소나 생성 → KPI/크레딧 종합.

중앙집중 멀티에이전트(매니저+작업에이전트) = agentic-RAG 구조.
비용은 게이트 라우팅(필요한 페르소나만) + 모델 티어링으로 통제하고,
모든 호출을 계측해 크레딧으로 환산한다(계측=과금, §6·§7).
"""
from . import personas, router
from .llm import Meter, get_provider


def run_geo(merchant, question: str = "") -> dict:
    """업체 베이스라인을 받아 지오가 종합 액션 + 크레딧 청구액을 산출."""
    card = merchant.baseline_card()
    provider = get_provider()
    meter = Meter()

    route = router.decide(card, question=question, provider=provider, meter=meter)

    actions = [personas.advise(p, card, provider, meter) for p in route["personas"]]

    return {
        "merchant": merchant.name,
        "overall_data_grade": card.get("overall_data_grade"),
        "routing": {
            "woke_personas": route["personas"],
            "reasons": route["reasons"],
            "woke_all": route["woke_all"],
            "router": route["router_model"],
            "gated_cost_note": "필요한 페르소나만 호출(게이트 라우팅, §6 #1)",
        },
        "actions": actions,
        "billing": {
            "provider": provider.name,
            # Stub(데모)엔 크레딧 미청구(REVIEW §2)
            "credits_charged": 0 if provider.name == "stub" else meter.credits(),
            "raw_cost_usd": round(meter.total_cost_usd(), 6),
            "calls": meter.breakdown(),
            "note": "계측=과금: 원가≤청구로 마진 보호(§7). provider=stub면 추정치.",
        },
        "disclaimer": card.get("disclaimer"),
    }
