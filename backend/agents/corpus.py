"""
인-코드 마케팅 과학 코퍼스 (빌드타임 RAG 최소본).

../references.md 의 교차검증된 [M1]~[M15]를 페르소나별 검색 가능한 형태로 보유한다.
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


CORPUS: list[Evidence] = [
    # 🎣 획득
    Evidence("M1", "acq", "성장은 충성강화보다 신규도달(침투)+정신적/물리적 가용성", "Sharp 2010, How Brands Grow"),
    Evidence("M2", "acq", "브랜드빌딩:활성화 ≈ 60:40 예산배분이 가장 효과적", "Binet & Field 2013, IPA"),
    Evidence("M3", "acq", "고각성·긍정 감정 콘텐츠가 공유(입소문)를 촉진", "Berger & Milkman 2012, JMR"),
    Evidence("M11", "acq", "리뷰 평점 1점↑ → 매출 5~9%↑ (독립업체 한정)", "Luca 2011, HBS WP"),
    Evidence("M15", "acq", "리뷰 개수·품질평가가 음식점 노출순위를 좌우(국내 외식)", "엄해정·진현정 2024, 호텔경영학연구"),
    # 💳 전환
    Evidence("M4", "cvr", "손실회피·프레이밍·기준점: '오늘 안 사면 손해'가 강력", "Kahneman & Tversky 1979, Econometrica"),
    Evidence("M5", "cvr", "9로 끝나는 가격이 현장실험 3건 모두 수요↑ (신상품 강함)", "Anderson & Simester 2003, QME"),
    Evidence("M6", "cvr", "사회적 증거·호혜·일관성이 전환을 끌어올림", "Cialdini & Goldstein 2004, Annu.Rev.Psychol."),
    Evidence("M12", "cvr", "반 별점(0.5)↑ → 매진 빈도 49%↑ (비선형)", "Anderson & Magruder 2012, Economic Journal"),
    Evidence("M14", "cvr", "국내·한국어 리뷰에서도 양·평점·긍정어 → 매출↑", "최자영 외 2020, 유통연구"),
    # 🔁 유지
    Evidence("M7", "ret", "이탈률 5%p↓ → 이익 25~85%↑", "Reichheld & Sasser 1990, HBR"),
    Evidence("M8", "ret", "RFM으로 단골 세분화 → CLV(생애가치) 추정(경량 통계)", "Fader·Hardie·Lee 2005, JMR"),
    Evidence("M9", "ret", "NPS(추천의향) 한 문항이 성장 최선예측", "Reichheld 2003, HBR"),
    Evidence("M10", "ret", "충성≠수익: 수익성 기준 세분화 필수(과대 리텐션 투자 방지)", "Reinartz & Kumar 2002, HBR"),
    Evidence("M13", "ret", "부정 리뷰가 긍정보다 무겁다 → 신속대응 우선", "Chevalier & Mayzlin 2006, JMR"),
]


def retrieve(persona: str, mids: list[str] | None = None) -> list[Evidence]:
    """페르소나 코퍼스 검색. mids 지정 시 해당 인용만(특정 갭 대응)."""
    items = [e for e in CORPUS if e.persona == persona]
    if mids:
        order = {m: i for i, m in enumerate(mids)}
        items = [e for e in items if e.mid in order]
        items.sort(key=lambda e: order[e.mid])
    return items
