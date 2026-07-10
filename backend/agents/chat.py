"""
Django 대화 엔진 — 세 에이전트와 멀티턴 대화(라우팅·협업·베이스라인 추출).

merchant.baseline_card()를 맥락으로, 지오가 메시지를 적합 페르소나로 라우팅(여러
영역이면 협업 2인), 각 페르소나가 대화이력 + [M#] 코퍼스로 답하고 핸드오프 안내.
영속(ChatMessage 저장)은 뷰가 담당.
"""
import re

from .corpus import retrieve
from .llm import Meter, get_provider
from .personas import PERSONA_META

KEYWORDS = {
    "acq": ["신규", "노출", "리뷰", "유입", "광고", "검색", "플레이스", "손님", "홍보", "인스타", "sns"],
    "cvr": ["전환", "객단가", "가격", "메뉴", "오퍼", "할인", "구매", "프로모션", "세트"],
    "ret": ["재방문", "단골", "멤버십", "쿠폰", "이탈", "nps", "리텐션", "적립"],
}
_PATTERNS = {
    "avg_rating": r"평점\s*(?:은|이|:|을)?\s*([0-5](?:\.\d)?)",
    "visit_to_purchase_rate": r"전환(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "revisit_rate": r"재방문(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "review_count": r"리뷰\s*(?:가|는|를|:)?\s*(\d{2,6})\s*(?:개|건)?",
    "aov": r"객단가\s*(?:은|는|이|:)?\s*([\d,]{4,})",
    "nps": r"(?i)nps\s*(?:은|는|이|:)?\s*(-?\d{1,3})",
}


def _card_summary(card):
    a, c, r = card.get("acquisition") or {}, card.get("conversion") or {}, card.get("retention") or {}
    return (f"평점 {a.get('avg_rating')}, 리뷰 {a.get('review_count')}, "
            f"객단가 {c.get('aov_krw')}, 전환율 {c.get('visit_to_purchase_rate_pct')}%, "
            f"재방문 {r.get('revisit_rate_pct')}%, NPS {r.get('nps')}")


# 목표/희망 발화("평점을 4.5로 올리고 싶어요")를 사실로 오인하지 않기 위한 의도어 가드(REVIEW §2)
_INTENT_RE = re.compile(r"싶|려면|할까|목표|어떻게|되고 싶")

# 비용·공격 방어(REVIEW §2): 서버가 신뢰 경계 — 프런트가 보낸 값은 절단한다
MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 12


def extract_updates(message):
    if _INTENT_RE.search(message or ""):
        return {}
    out = {}
    for field, pat in _PATTERNS.items():
        m = re.search(pat, message or "")
        if m:
            out[field] = m.group(1).replace(",", "")
    return out


def route(message, forced, provider, meter):
    if forced in ("acq", "cvr", "ret"):
        return [forced]
    low = (message or "").lower()
    scores = {p: sum(1 for kw in kws if kw in low) for p, kws in KEYWORDS.items()}
    ranked = [p for p in sorted(scores, key=lambda x: scores[x], reverse=True) if scores[p] > 0]
    if len(ranked) >= 2:
        return ranked[:2]
    if ranked:
        return ranked
    if provider.name != "stub":
        text, usage = provider.complete(
            model=provider.router_model,
            system="마케팅 의도 분류기. acq(획득)/cvr(전환)/ret(유지) 중 관련된 것만 콤마로 답하라.",
            prompt=message, max_tokens=12)
        meter.add(usage)
        picks = [p for p in ("acq", "cvr", "ret") if p in text]
        if picks:
            return picks[:2]
    return ["cvr"]


def _label(persona):
    meta = PERSONA_META[persona]
    return f"{meta['emoji']} {meta['name']}", meta["kpi"]


def _system(persona, merchant_name, card, collab=None):
    label, kpi = _label(persona)
    cites = "\n".join(f"- [{e.mid}] {e.claim} ({e.cite})" for e in retrieve(persona))
    others = ", ".join(f"{PERSONA_META[p]['emoji']} {PERSONA_META[p]['name']}"
                       for p in ("acq", "cvr", "ret") if p != persona)
    base = (f"너는 소상공인 '{merchant_name}'의 마케팅 {label} 담당 에이전트다. 담당 KPI: {kpi}.\n"
            f"베이스라인: {_card_summary(card)}.\n"
            f"근거 코퍼스(관련 시 반드시 [M#] 인용):\n{cites}\n"
            f"너는 획득·전환·유지 3인 팀의 일원이다. 네 영역 밖이면 {others} 에이전트에게 넘기라고 안내하라.\n"
            "사장님과 대화하듯 간결하고 실행가능하게 답하라. 2~4문장. 출처를 댈 수 없는 단정은 금지.")
    if collab:
        base += (f"\n\n[협업] 동료 에이전트가 먼저 답했다:\n\"{collab}\"\n"
                 "중복은 피하고 네 전문영역 관점에서 보완·연결하라(시너지 한 줄).")
    return base


def run_chat(merchant, history, message, forced=None):
    card = merchant.baseline_card()
    provider = get_provider()
    meter = Meter()
    message = (message or "")[:MAX_MESSAGE_CHARS]
    personas = route(message, forced, provider, meter)

    trimmed = [
        {"role": h.get("role"), "text": (h.get("text") or "")[:MAX_MESSAGE_CHARS]}
        for h in (history or [])[-MAX_HISTORY_MESSAGES:]
    ]
    messages = trimmed + [{"role": "user", "text": message}]
    turns, collab = [], None
    for p in personas:
        label, kpi = _label(p)
        system = _system(p, merchant.name, card, collab)
        reply, usage = provider.chat(system=system, messages=messages)
        meter.add(usage)
        turns.append({"persona": p, "label": label, "kpi": kpi, "reply": reply,
                      "citations": [e.mid for e in retrieve(p)]})
        collab = f"{label}: {reply}"

    # Stub(데모)엔 크레딧을 청구하지 않는다(REVIEW §2 — 정직한 과금)
    credits = 0 if provider.name == "stub" else meter.credits()
    return {
        "turns": turns,
        "updated_fields": extract_updates(message),
        "billing": {"provider": provider.name, "credits_charged": credits,
                    "raw_cost_usd": round(meter.total_cost_usd(), 6)},
    }
