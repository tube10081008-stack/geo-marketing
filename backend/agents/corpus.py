"""
인-코드 마케팅 과학 코퍼스 (빌드타임 RAG 최소본).

../references.md 의 교차검증된 [M1]~[M22]를 페르소나별 검색 가능한 형태로 보유한다.
PoC 단계라 in-memory; 운영은 pgvector Dense 검색으로 승격(orchestrator §6).
각 항목은 *출처를 댈 수 있는* 근거이며, 페르소나는 답변 시 반드시 인용한다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    mid: str          # [M#]
    persona: str      # acq | cvr | ret
    claim: str        # 한 줄 핵심
    cite: str         # 인용 표기


# 문구 원칙(REVIEW.md §1): 원 연구의 맥락(국가·업태·연도)을 벗기지 않는다.
# 수치는 "그 연구에서 그랬다"이지 한국 소상공인에 대한 약속이 아니다.
CORPUS: list[Evidence] = [
    # 🎣 획득
    Evidence("M1", "acq", "매출성장의 주동력은 신규도달(침투)+정신적/물리적 가용성 — 이익 레버리지(M7)와는 다른 축", "Sharp 2010, How Brands Grow"),
    Evidence("M2", "acq", "브랜드빌딩:활성화 ≈ 60:40 — 단 IPA 수상사례집(대기업 중심) 평균, 방향 참고용", "Binet & Field 2013, IPA"),
    Evidence("M3", "acq", "고각성·긍정 감정 콘텐츠가 공유(입소문)를 촉진", "Berger & Milkman 2012, JMR"),
    Evidence("M11", "acq", "평점 1점↑→매출 5~9%↑ — 美 시애틀 독립식당 데이터, 방향성 근거(국내 수치 아님)", "Luca 2011, HBS WP"),
    Evidence("M15", "acq", "리뷰 개수·품질평가가 음식점 노출순위를 좌우(국내 외식)", "엄해정·진현정 2024, 호텔경영학연구"),
    # 💳 전환
    Evidence("M4", "cvr", "손실회피·프레이밍·기준점 — 오퍼 카피는 A/B 실험으로 검증 후 채택", "Kahneman & Tversky 1979, Econometrica"),
    Evidence("M5", "cvr", "9-끝자리 가격이 현장실험 3건 수요↑(美 통판) — 세일 단서와 병용 시 약화, 남발 금지", "Anderson & Simester 2003, QME"),
    Evidence("M6", "cvr", "사회적 증거·호혜·일관성이 전환을 끌어올림", "Cialdini & Goldstein 2004, Annu.Rev.Psychol."),
    Evidence("M12", "cvr", "반 별점(0.5)↑ → 예약 매진 +19%p(상대 49%) — 美 예약식당 맥락", "Anderson & Magruder 2012, Economic Journal"),
    Evidence("M14", "cvr", "국내·한국어 리뷰에서도 양·평점·긍정어 → 매출↑", "최자영 외 2020, 유통연구"),
    # 🔁 유지
    Evidence("M7", "ret", "이탈률 5%p↓→이익 25~85%↑ — 1990년 특정 산업 사례치, 일반화 금지(방향성 근거)", "Reichheld & Sasser 1990, HBR"),
    Evidence("M8", "ret", "RFM으로 단골 세분화 → CLV(생애가치) 추정(경량 통계)", "Fader·Hardie·Lee 2005, JMR"),
    Evidence("M9", "ret", "NPS 한 문항은 간편한 보조지표 — '성장 최선예측' 주장은 재현 실패[M16], 단독 근거 금지", "Reichheld 2003, HBR"),
    Evidence("M10", "ret", "충성≠수익: 수익성 기준 세분화 필수(과대 리텐션 투자 방지)", "Reinartz & Kumar 2002, HBR"),
    Evidence("M13", "ret", "부정 리뷰가 긍정보다 무겁다 → 신속대응 우선", "Chevalier & Mayzlin 2006, JMR"),
    Evidence("M16", "ret", "(반론) NPS 우월성 재현 실패 — 노르웨이 21개사 종단, MSI Root Award", "Keiningham et al. 2007, J. of Marketing"),

    # ── 편중 보정 확장(M17~M22): 업종(카페·소매·은행)·지역(국내)·플랫폼(배달앱)·메커니즘(로열티·추천) ──
    # 🎣 획득 — 추천·플랫폼 채널
    Evidence("M18", "acq", "추천(리퍼럴)로 획득한 고객이 가치 ≥16%↑·이탈 ~18%↓ — 독일 은행 3년 종단(업종 전이 주의)", "Schmitt·Skiera·Van den Bulte 2011, J. of Marketing"),
    Evidence("M20", "acq", "배달플랫폼이 포장매출↑+매장방문 스필오버 — 효과 이질적(체인>독립 약4배)·수익성 잠식 주의", "Li & Wang 2025, Management Science"),
    Evidence("M21", "acq", "(국내) 배달앱 이용 음식점이 코로나 오프라인 매출감소의 절반 이상 보전 — 대구 사업체 신용카드 데이터", "이상원·전현배 2022, 경제학연구"),
    # 🔁 유지 — 로열티·목표구배(카페·소매 특화)
    Evidence("M17", "ret", "적립 목표에 다가갈수록 구매가속(목표구배) — 카페 '커피10잔' 로열티카드 현장데이터(첫 보상 후 일시 이완)", "Kivetz·Urminsky·Zheng 2006, JMR"),
    Evidence("M19", "ret", "로열티프로그램=동적 인센티브가 상당수 고객의 연간구매↑ — 단 설계 의존", "Lewis 2004, JMR"),
    Evidence("M22", "ret", "(반론) 로열티프로그램 도입 효과는 대체로 단기 — 카테고리 지출 재분배 후 감소(소매)", "Lin & Bowman 2022, J. of Retailing and Consumer Services"),
]


def retrieve(persona: str, mids: list[str] | None = None) -> list[Evidence]:
    """페르소나 코퍼스 검색. mids 지정 시 해당 인용만(특정 갭 대응)."""
    items = [e for e in CORPUS if e.persona == persona]
    if mids:
        order = {m: i for i, m in enumerate(mids)}
        items = [e for e in items if e.mid in order]
        items.sort(key=lambda e: order[e.mid])
    return items
