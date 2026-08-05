"""
3 페르소나 — 베이스라인 + 코퍼스([M#]) → 근거 인용 액션 생성.

각 페르소나는 ① 자기 갭에 맞는 코퍼스 청크 검색 ② 측정가능한 타깃 액션 생성
③ 반드시 [M#] 출처 인용. 생성 phrasing은 생성 티어 모델(Sonnet)로 자연화하되,
스텁 환경에서도 grounded 텍스트가 결정적으로 나오도록 호출부가 초안을 만든다.
"""
from . import rules
from .corpus import retrieve

# 액션 1개 · 400자 목표에 여유를 둔 출력 상한.
# 900이던 시절 프롬프트에 분량 규율이 없어 장문이 생성되다 침묵 절단됐다.
ADVISE_MAX_TOKENS = 512

PERSONA_META = {
    "acq": {"emoji": "🎣", "name": "획득", "kpi": "신규유입·노출·리뷰수"},
    "cvr": {"emoji": "💳", "name": "전환", "kpi": "전환율·객단가·평점"},
    "ret": {"emoji": "🔁", "name": "유지", "kpi": "재방문율·LTV·NPS"},
    # 오케스트레이터 — 직접 호명("복어야")·리포트 조회 등 메타 질문에 본인이 답한다
    "fugu": {"emoji": "🐡", "name": "복어 Fugu", "kpi": "오케스트레이션·리포트·종합"},
    # 세무 가이드 — 개념·일정·절세제도·준비 체크리스트(세무대리 아님, 세무사법 준수)
    "cora": {"emoji": "🧾", "name": "코라 Cora", "kpi": "절세·세무 일정·증빙 준비"},
}


def _draft(persona: str, card: dict) -> tuple[str, list[str]]:
    """페르소나별 결정적 액션 초안 + 사용 인용 목록."""
    acq = card.get("acquisition") or {}
    cvr = card.get("conversion") or {}
    ret = card.get("retention") or {}

    if persona == "acq":
        rating = acq.get("avg_rating")
        reviews = acq.get("review_count")
        mids = ["M11", "M15", "M1"]
        target = f"평점 {rating}→{round((rating or 4.0)+0.2, 1)}, 리뷰수 +20%(4주)"
        action = (
            f"네이버 플레이스 노출·리뷰 보강. 만족 고객에게 영수증 리뷰 요청 도입. "
            f"목표: {target}. 근거: 평점↑→매출↑(美 독립식당 5~9%, 방향성)[M11], "
            f"리뷰 개수가 노출순위 좌우(국내)[M15], 신규도달=성장 동력[M1]."
        )
    elif persona == "cvr":
        rating = acq.get("avg_rating")
        conv = cvr.get("visit_to_purchase_rate_pct")
        aov = cvr.get("aov_krw")
        mids = ["M12", "M5", "M4"]
        target = f"전환율 {conv}%→{round((conv or 55)+3, 1)}%, 객단가 유지"
        action = (
            f"평점 0.1~0.2 상향 + '오늘만' 손실프레임 오퍼 A/B. 가격 끝자리 9 적용 실험. "
            f"목표: {target}(객단가 {aov}원 기준). 근거: 반별점↑→예약매진 +19%p(美)[M12], "
            f"9-끝자리 수요↑(세일 병용 시 약화)[M5], 손실회피 프레이밍(A/B 검증 전제)[M4]."
        )
    else:  # ret
        revisit = ret.get("revisit_rate_pct")
        nps = ret.get("nps")
        mids = ["M7", "M8", "M13", "M10", "M16"]
        target = f"재방문율 {revisit}%→{round((revisit or 30)+5, 1)}%, NPS {nps}→{(nps or 30)+5}"
        action = (
            f"RFM 단골 세분화 후 이탈 임박 세그먼트에 재방문 쿠폰(수익성 세그먼트 우선[M10]). "
            f"부정리뷰 24h 내 응대 룰. 목표: {target}. "
            f"근거: 이탈↓→이익↑(산업별 사례 25~85%)[M7], RFM·CLV 세분화[M8], "
            f"부정리뷰가 더 무겁다→신속대응[M13]. NPS는 보조지표로만[M9→M16]."
        )
    return action, mids


def advise(persona: str, card: dict, provider, meter) -> dict:
    """한 페르소나의 근거기반 액션. 생성 티어 모델로 phrasing(있을 때)."""
    draft, mids = _draft(persona, card)
    evidences = retrieve(persona, mids)
    cite_lines = "\n".join(f"- [{e.mid}] {e.claim} ({e.cite})" for e in evidences)

    system = (
        f"너는 소상공인 마케팅 {PERSONA_META[persona]['name']} 전문가다. "
        f"담당 KPI: {PERSONA_META[persona]['kpi']}. "
        "반드시 아래 근거의 [M#]를 인용하고, 측정가능한 목표를 제시하라. "
        "출처를 댈 수 없는 단정은 금지.\n"
        # 분량 규율이 없어 모델이 장문 보고서를 쓰다 상한에 걸려 문장 중간에서
        # 끊겼다(실제 사고). 카드 UI에 들어갈 크기로 못박는다.
        "[분량 — 반드시 지켜라] 액션 1개만, 전체 400자 이내로 써라. "
        "제목·소제목·표를 만들지 말고 바로 실행 문장으로 시작하라. "
        "배경 설명과 일반론은 빼고 '무엇을 · 어떻게 잴지'만 남겨라. "
        "절대 문장 중간에서 끝내지 마라 — 400자 안에 마무리되게 계획해서 써라."
        + rules.CLAIM_STRENGTH + rules.profit_rules(card) + rules.HONESTY
    )
    prompt = (
        f"[베이스라인]\n{card}\n\n[근거 코퍼스]\n{cite_lines}\n\n"
        f"[초안 액션]\n{draft}\n\n위 초안을 사장님이 바로 실행할 액션 1개로 다듬어라."
    )
    text, usage = provider.complete(
        model=provider.generator_model, system=system, prompt=prompt,
        # 400자 목표에 여유를 둔 상한(한국어 약 1.5~2자/토큰).
        # 규율을 지킨 답변은 잘리지 않고, 폭주한 답변만 안내와 함께 걸린다.
        max_tokens=ADVISE_MAX_TOKENS,
    )
    meter.add(usage)

    # 스텁이면 prompt를 그대로 돌려주므로 결정적 draft를 표면화
    surfaced = draft if provider.name == "stub" else (text or draft)
    return {
        "persona": persona,
        "label": f"{PERSONA_META[persona]['emoji']} {PERSONA_META[persona]['name']}",
        "kpi": PERSONA_META[persona]["kpi"],
        "action": surfaced,
        "citations": [e.mid for e in evidences],
        "generator_model": provider.generator_model,
    }
