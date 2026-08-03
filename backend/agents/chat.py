"""
Django 대화 엔진 — 세 에이전트와 멀티턴 대화(라우팅·협업·베이스라인 추출).

merchant.baseline_card()를 맥락으로, 복어(Fugu)가 메시지를 적합 페르소나로
라우팅(여러 영역이면 협업 2인), 각 페르소나가 [장기기억 요약 + 최근 대화창 +
[M#] 코퍼스]로 답하고 핸드오프 안내. 영속(ChatMessage 저장)은 뷰가 담당.

장기기억(요약 컴팩션): 최근 MAX_HISTORY_MESSAGES개만 원문으로 보내고, 그보다
오래된 대화는 저가 라우터 모델로 병합 요약(maybe_compact)해 Merchant.chat_summary
에 영속 → 전체 이력이 DB에 남으면서도 토큰 소모는 상수로 유지된다.
"""
import re

from .corpus import retrieve
from .llm import CHAT_MAX_TOKENS, Meter, get_provider
from .personas import PERSONA_META

KEYWORDS = {
    "acq": ["신규", "노출", "리뷰", "유입", "광고", "검색", "플레이스", "손님", "홍보", "인스타", "sns"],
    "cvr": ["전환", "객단가", "가격", "메뉴", "오퍼", "할인", "구매", "프로모션", "세트"],
    # 멤버십·RFM·CLV·로열티는 유지(ret)의 핵심 주제 — 담당 코퍼스(M8·M17·M19)가 여기 있다.
    # '멤버쉽/맴버쉽'은 사용자가 흔히 쓰는 비표준 철자라 반드시 함께 매칭(과거 유지 미라우팅 버그).
    "ret": ["재방문", "단골", "멤버십", "멤버쉽", "맴버십", "맴버쉽", "member", "쿠폰", "이탈",
            "nps", "리텐션", "적립", "rfm", "clv", "ltv", "crm", "생애가치", "로열티", "충성", "세분화"],
}
_PATTERNS = {
    "avg_rating": r"평점\s*(?:은|이|:|을)?\s*([0-5](?:\.\d)?)",
    "visit_to_purchase_rate": r"전환(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "revisit_rate": r"재방문(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "review_count": r"리뷰\s*(?:가|는|를|:)?\s*(\d{2,6})\s*(?:개|건)?",
    "aov": r"객단가\s*(?:은|는|이|:)?\s*([\d,]{4,})",
    "nps": r"(?i)nps\s*(?:은|는|이|:)?\s*(-?\d{1,3})",
    "regular_ratio": r"단골\s*(?:비중|비율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "monthly_customers": r"(?:월\s*)?고객\s*(?:수)?\s*(?:는|은|가|:)?\s*([\d,]{2,7})\s*명",
}


def _card_summary(card):
    a, c, r = card.get("acquisition") or {}, card.get("conversion") or {}, card.get("retention") or {}
    return (f"평점 {a.get('avg_rating')}, 리뷰 {a.get('review_count')}, "
            f"객단가 {c.get('aov_krw')}, 월고객수 {c.get('monthly_customers')}, "
            f"재방문 {r.get('revisit_rate_pct')}%, 단골비중 {r.get('regular_ratio_pct')}%")


# 목표/희망 발화("평점을 4.5로 올리고 싶어요")를 사실로 오인하지 않기 위한 의도어 가드(REVIEW §2)
_INTENT_RE = re.compile(r"싶|려면|할까|목표|어떻게|되고 싶")

# 복어 직접 호명 / 리포트 상태성 질문 → 오케스트레이터가 직접 답한다
_FUGU_RE = re.compile(r"복어|fugu|후구", re.I)
_REPORT_RE = re.compile(r"리포트|보고서|report", re.I)

# 세무 질문 → 🧾 코라(Cora) 단독 응답(마케팅 협업 미발동)
_CORA_RE = re.compile(
    r"코라|cora|세금|세무|부가세|부가가치세|종소세|종합소득세|절세|세액공제|소득공제|"
    r"세금계산서|현금영수증|기장|장부|간이과세|일반과세|노란우산|가산세|원천세|"
    r"4대\s*보험|홈택스|필요경비|증빙", re.I)

# 유령 약속("복어님께 전달하겠다") 대화 붕괴 방지 — 모든 페르소나 공통 규칙
_HONESTY = (
    "\n[정직 규칙] 너는 이 답변 밖에서 어떤 행동도 실행할 수 없다. "
    "'복어님께 전달하겠다', '곧 공유될 예정' 같은 실행되지 않는 백그라운드 작업을 약속하지 마라. "
    "다른 에이전트나 복어의 발화를 지어내 끼워 넣지 마라. 확인할 수 없는 것은 모른다고 답하라.")

# 비용·공격 방어(REVIEW §2): 서버가 신뢰 경계 — 프런트가 보낸 값은 절단한다
MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 12
# 최근 창 밖의 미요약 메시지가 이만큼 쌓이면 장기기억으로 병합(저가 모델 1콜)
COMPACT_TRIGGER = 8
SUMMARY_MAX_CHARS = 800


def extract_updates(message):
    if _INTENT_RE.search(message or ""):
        return {}
    out = {}
    for field, pat in _PATTERNS.items():
        m = re.search(pat, message or "")
        if m:
            out[field] = m.group(1).replace(",", "")
    return out


# 본문에서 인용 토큰 추출 — [M1]·[T3] 형태. 검증(코퍼스 실재)에 사용.
_CITE_TOKEN_RE = re.compile(r"\[([MT]\d+)\]")


def _sanitize_citations(reply, persona):
    """답변 본문의 [M#]/[T#] 중 '이 페르소나에게 제공된 코퍼스에 실재하는' 것만 남긴다.

    코퍼스에 없는 번호(환각)나 다른 담당의 번호(오배정)는 본문에서 제거하고 칩에도
    넣지 않는다 — 지어낸 인용을 코드로 차단해 '검증된 근거만'을 강제(제품 1번 원칙).
    반환: (정리된 본문, 실제 사용된 인용 리스트 — 등장 순서·중복 제거).
    """
    allowed = {e.mid for e in retrieve(persona)}
    used = []

    def _keep(m):
        mid = m.group(1)
        if mid in allowed:
            if mid not in used:
                used.append(mid)
            return m.group(0)
        return ""  # 미검증 인용 토큰 제거

    cleaned = _CITE_TOKEN_RE.sub(_keep, reply or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)      # 토큰 제거로 생긴 이중 공백
    cleaned = re.sub(r"\s+([,.)])", r"\1", cleaned)   # 구두점 앞 공백 정리
    return cleaned.strip(), used


def route(message, forced, provider, meter):
    if forced in ("acq", "cvr", "ret", "cora"):
        return [forced]
    low = (message or "").lower()
    # 복어 직접 호명, 또는 짧은 리포트 상태성 질문("리포트 조회 가능해?") → 오케스트레이터
    if _FUGU_RE.search(low) or (_REPORT_RE.search(low) and len(message) < 40):
        return ["fugu"]
    # 세무 질문 → 코라 단독(잘못된 마케팅 협업·이중과금 방지)
    if _CORA_RE.search(low):
        return ["cora"]
    scores = {p: sum(1 for kw in kws if kw in low) for p, kws in KEYWORDS.items()}
    ranked = [p for p in sorted(scores, key=lambda x: scores[x], reverse=True) if scores[p] > 0]
    if len(ranked) >= 2:
        # "됐어?" 같은 짧은 후속 발화엔 2인 협업(=2배 과금)을 발동하지 않는다
        return ranked[:2] if len(message) >= 24 else ranked[:1]
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
            return picks[:2] if len(message) >= 24 else picks[:1]
    return ["cvr"]


def _label(persona):
    meta = PERSONA_META[persona]
    return f"{meta['emoji']} {meta['name']}", meta["kpi"]


def _system(persona, merchant, card, collab=None, summary="", report_note="",
            actions_note="", benchmark_note=""):
    where = f"{merchant.get_industry_display()}, {merchant.location}"
    if persona == "cora":
        # 세무 가이드 — 개념·일정·절세제도·준비까지만. 세무대리 금지(세무사법 준수)
        cites = "\n".join(f"- [{e.mid}] {e.claim} ({e.cite})" for e in retrieve("cora"))
        base = (f"너는 🧾 코라(Cora), 소상공인 '{merchant.name}'({where})의 세무 가이드 에이전트다.\n"
                "역할: 한국 세법·상법의 개념 설명, 세무 일정 안내, 절세 제도 소개, 신고 전 준비 체크리스트.\n"
                f"검증된 근거(관련 시 반드시 [T#] 인용):\n{cites}\n"
                f"참고 베이스라인: {_card_summary(card)}.\n"
                "[법적 경계 — 세무사법 준수] 너는 세무사가 아니다. 세무대리(신고 대행, 개별 세액 확정 계산, "
                "불복 대리)는 할 수 없으며 하겠다고 말해서도 안 된다. 개별 사안의 확정 판단이 필요하면 "
                "세무사(전국 무료 '마을세무사' 제도 포함)나 국세청 상담센터(126)를 안내하라.\n"
                "[개정 주의] 세법은 매년 바뀐다. 수치·기한에는 근거의 기준연도를 붙이고, 근거 목록에 없는 "
                "수치는 단정하지 말고 '홈택스/세무사 확인 필요'라고 명시하라.\n"
                "마케팅 질문이면 🐡 복어 팀(획득·전환·유지)으로 안내하라.\n"
                "[분량 규율] 기본은 간결(2~5문장). 체크리스트·가이드 요청도 1,000자 이내로 "
                "중요도순 3~4개 항목까지만 답하고, 남은 주제는 마지막 줄에 "
                "\"○○는 이어서 물어보세요\"로 예고하라. 절대 문장 중간에서 끝내지 마라.")
    elif persona == "fugu":
        # 오케스트레이터 본인 — 리포트·팀 상태 등 메타 질문에 직접 답한다
        base = (f"너는 🐡 복어(Fugu), 소상공인 '{merchant.name}'({where})의 마케팅 오케스트레이터다.\n"
                f"🎣 획득 · 💳 전환 · 🔁 유지 3인 마케팅 팀을 지휘하고, 🧾 세무 가이드 코라(Cora)와 협업한다.\n"
                f"베이스라인: {_card_summary(card)}.\n"
                + (f"[최신 딥리포트(발췌)]\n{report_note}\n" if report_note
                   else "완료된 딥리포트가 아직 없다 — 있는 것처럼 말하지 마라.\n")
                + "사장님 질문에 종합 관점으로 지금 바로 답하고, 실행 디테일이 필요하면 "
                  "담당 에이전트를 지목해 그쪽에 물어보시라고 안내하라. 2~5문장, 관련 시 [M#] 인용.")
    else:
        label, kpi = _label(persona)
        cites = "\n".join(f"- [{e.mid}] {e.claim} ({e.cite})" for e in retrieve(persona))
        others = ", ".join(f"{PERSONA_META[p]['emoji']} {PERSONA_META[p]['name']}"
                           for p in ("acq", "cvr", "ret") if p != persona)
        base = (f"너는 소상공인 '{merchant.name}'({where})의 마케팅 {label} 담당 에이전트다. 담당 KPI: {kpi}.\n"
                f"업종이 '{merchant.get_industry_display()}'임을 전제로 답하라 — 상호명으로 업종을 추측하지 마라.\n"
                f"오케스트레이터는 🐡 복어(Fugu)이며 너는 복어가 이끄는 획득·전환·유지 3인 팀의 일원이다.\n"
                f"베이스라인: {_card_summary(card)}.\n"
                f"근거 코퍼스(관련 시 반드시 [M#] 인용):\n{cites}\n"
                f"네 영역 밖이면 {others} 에이전트에게, 세금·세무 질문이면 🧾 코라(Cora)에게 넘기라고 안내하라.\n"
                "사장님과 대화하듯 간결하고 실행가능하게 답하라. 2~4문장. 출처를 댈 수 없는 단정은 금지.")
        if report_note:
            base += f"\n\n[최신 딥리포트(발췌) — 사장님이 언급한 리포트]\n{report_note}"
    if actions_note and persona != "cora":
        base += (f"\n\n[진행 중 실행안 — 조언→실측 루프]\n{actions_note}\n"
                 "관련 대화면 진행 상황을 한 번 자연스럽게 확인하고, 타깃 지표의 현재 값을 "
                 "물어보라(사장님이 숫자를 말하면 자동 기록된다). 억지로 끼워넣지는 마라.")
    if benchmark_note and persona != "cora":
        base += (f"\n\n[업종 벤치마크 — 익명 집계, 계절성 통제(diff-in-diff)]\n{benchmark_note}\n"
                 "'차분(%p)'이 업종 전체 추세를 뺀 순수 개선 신호다. 성과 평가·조언 조정 시 "
                 "절대 증가율이 아니라 이 차분을 근거로 말하라. 표본(n)이 작으면 참고 수준임을 밝혀라.")
    base += _HONESTY
    if summary:
        base += f"\n\n[장기기억 — 이전 대화 요약]\n{summary[:SUMMARY_MAX_CHARS]}"
    if collab:
        base += (f"\n\n[협업] 동료 에이전트가 먼저 답했다:\n\"{collab}\"\n"
                 "동료 답변을 재인용·복사·요약반복하지 마라. 네 전문영역에서 보탤 내용만 1~3문장. "
                 "보탤 것이 없으면 '덧붙일 내용은 없습니다' 한 문장으로만 끝내라.")
    return base


def run_chat(merchant, history, message, forced=None, provider=None, meter=None,
             report_note="", actions_note="", benchmark_note=""):
    card = merchant.baseline_card()
    provider = provider or get_provider()
    meter = meter if meter is not None else Meter()
    message = (message or "")[:MAX_MESSAGE_CHARS]
    personas = route(message, forced, provider, meter)

    # 리포트 발췌는 관련 턴에만 주입(토큰 절약): 복어 턴 or 사장님이 리포트를 언급했을 때
    mentions_report = bool(_REPORT_RE.search(message))

    trimmed = [
        {"role": h.get("role"), "text": (h.get("text") or "")[:MAX_MESSAGE_CHARS]}
        for h in (history or [])[-MAX_HISTORY_MESSAGES:]
    ]
    messages = trimmed + [{"role": "user", "text": message}]
    turns, collab = [], None
    for p in personas:
        label, kpi = _label(p)
        note = report_note if (p == "fugu" or mentions_report) else ""
        system = _system(p, merchant, card, collab,
                         summary=merchant.chat_summary, report_note=note,
                         actions_note=actions_note, benchmark_note=benchmark_note)
        # 코라는 일정·체크리스트형 답이 길다 — 상한 3배 + 프롬프트 길이규율 + 절단감지 3중 방어
        reply, usage = provider.chat(
            system=system, messages=messages,
            max_tokens=CHAT_MAX_TOKENS * 3 if p == "cora" else CHAT_MAX_TOKENS)
        meter.add(usage)
        # 검증된 인용만: 본문의 [M#]/[T#] 중 이 담당 코퍼스에 실재하는 것만 남기고
        # 나머지(환각·오배정)는 본문·칩에서 제거 → 정직 원칙을 코드로 강제.
        reply, used_cites = _sanitize_citations(reply, p)
        turns.append({"persona": p, "label": label, "kpi": kpi, "reply": reply,
                      "citations": used_cites})
        collab = f"{label}: {reply}"[:600]  # 다음 페르소나 참조용 — 길이 상한(비용 방어)

    return {
        "turns": turns,
        "updated_fields": extract_updates(message),
        # 과금(포인트 차감·잔액)은 뷰가 Wallet과 함께 채운다
        "billing": {"provider": provider.name,
                    "raw_cost_usd": round(meter.total_cost_usd(), 6)},
    }


def maybe_compact(merchant, provider, meter):
    """최근 창 밖에 미요약 메시지가 COMPACT_TRIGGER개 이상 쌓이면 장기기억으로 병합.

    전체 원문은 ChatMessage(DB)에 영구 보존되고, LLM에는 [요약 + 최근 창]만
    전달된다 — 대화가 아무리 길어져도 턴당 토큰 소모가 상수로 유지된다.
    반환: 요약 갱신 여부.
    """
    from onboarding.models import ChatMessage

    qs = merchant.messages.exclude(role=ChatMessage.Role.SYSTEM).order_by("-pk")
    recent_ids = list(qs.values_list("pk", flat=True)[:MAX_HISTORY_MESSAGES])
    cutoff = min(recent_ids) if recent_ids else 0
    stale = list(
        qs.filter(pk__gt=merchant.chat_summary_upto, pk__lt=cutoff).order_by("pk"))
    if len(stale) < COMPACT_TRIGGER:
        return False

    lines = "\n".join(f"{m.role}: {m.text[:300]}" for m in stale)
    prev = merchant.chat_summary or "(없음)"
    if provider.name == "stub":
        # 무키 환경 — 결정적 컴팩션(원문 앞부분 유지)으로 파이프라인만 검증
        summary = (merchant.chat_summary + " | " + lines.replace("\n", " / "))[-SUMMARY_MAX_CHARS:]
    else:
        text, usage = provider.complete(
            model=provider.router_model,
            system=("소상공인 대화 장기기억 요약기. 기존 요약과 새 대화를 병합해 "
                    "가게 사실·지표 수치·목표·진행 중 실행안·사장님 선호만 남기고 "
                    f"{SUMMARY_MAX_CHARS}자 이내 한국어 개조식으로 요약하라."),
            prompt=f"[기존 요약]\n{prev}\n\n[새 대화]\n{lines}",
            max_tokens=500)
        meter.add(usage)
        summary = (text or prev)[:SUMMARY_MAX_CHARS]
    merchant.chat_summary = summary
    merchant.chat_summary_upto = stale[-1].pk
    merchant.save(update_fields=["chat_summary", "chat_summary_upto"])
    return True
