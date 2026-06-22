"""
지오 라우터 — 게이트형 라우팅 (orchestrator §6 #1).

베이스라인 카드에서 *약한 KPI 노드*를 감지해 **필요한 페르소나만** 깨운다.
항상 셋 다 호출하지 않는 것이 비용 통제의 1순위.
선택적으로 고객 자유질의를 저가 라우터 모델로 의도분류(있을 때만).
"""

# 갭 판정 임계값(베이스라인 등급이 낮으면 추정이므로 보수적으로 깨움)
THRESHOLDS = {
    "rating_weak": 4.3,      # 평점 약함 → 획득+전환 [M11][M12][M14]
    "conv_weak": 60.0,       # 전환율 약함(%) [M12]
    "revisit_weak": 35.0,    # 재방문 약함(%) [M7]
    "nps_weak": 30,          # NPS 약함 [M9]
}


def decide(card: dict, question: str = "", provider=None, meter=None) -> dict:
    """깨울 페르소나 + 사유 결정. 반환: {personas:[...], reasons:{...}, woke_all:bool}."""
    acq = card.get("acquisition") or {}
    cvr = card.get("conversion") or {}
    ret = card.get("retention") or {}

    woke: dict[str, str] = {}

    rating = acq.get("avg_rating")
    if rating is None or rating < THRESHOLDS["rating_weak"]:
        woke["acq"] = f"평점/노출 약함(rating={rating}) → 발견·리뷰 보강"

    conv = cvr.get("visit_to_purchase_rate_pct")
    if conv is None or conv < THRESHOLDS["conv_weak"]:
        woke["cvr"] = f"전환율 약함(conv={conv}%) → 평점·오퍼 최적화"
    elif rating is None or rating < THRESHOLDS["rating_weak"]:
        # 평점이 전환에도 직결([M12]) → 전환도 함께
        woke.setdefault("cvr", "평점이 전환에 직결 → 함께 점검")

    revisit = ret.get("revisit_rate_pct")
    nps = ret.get("nps")
    if revisit is None or revisit < THRESHOLDS["revisit_weak"] or (nps is not None and nps < THRESHOLDS["nps_weak"]):
        woke["ret"] = f"재방문/NPS 약함(revisit={revisit}%, nps={nps}) → 리텐션"

    # 자유질의가 있으면 저가 라우터 모델로 의도 키워드 보강(선택)
    if question and provider is not None:
        text, usage = provider.complete(
            model=provider.router_model,
            system="너는 마케팅 의도 분류기다. 질문을 acq(획득)/cvr(전환)/ret(유지) 중 관련된 것만 콤마로 답하라.",
            prompt=question,
            max_tokens=20,
        )
        if meter is not None:
            meter.add(usage)
        for key in ("acq", "cvr", "ret"):
            if key in text and key not in woke:
                woke[key] = f"고객 질의 의도({key})"

    # 아무 갭도 없으면 최소 1개(전환)는 깨워 quick-win 제공
    if not woke:
        woke["cvr"] = "뚜렷한 약점 없음 → 전환 미세최적화로 quick-win"

    return {
        "personas": list(woke.keys()),
        "reasons": woke,
        "woke_all": len(woke) == 3,
        "router_model": (provider.router_model if (question and provider) else "rule-based(무료)"),
    }
