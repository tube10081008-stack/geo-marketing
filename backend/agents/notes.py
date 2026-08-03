"""2단 근거 — references.md의 상세 노트를 '필요할 때만' 꺼내 쓴다.

배경: corpus.py는 근거를 한 줄(≈86자)로만 갖고 있어 에이전트가 얕게 답하고
세부를 지어냈다. references.md에는 이미 항목당 ~850자의 교차검증된 노트
(서지·핵심·우리에게·적용)가 있는데 런타임에서 전혀 쓰이지 않았다.

설계:
- 1단(항상 주입) = corpus.py 한 줄 색인. 인용 규율 유지, ~300토큰.
- 2단(질문 관련 시) = 여기의 상세 노트. 로컬 조회라 API 비용·지연 0,
  페이월·저작권 문제도 없다(우리가 쓴 글).
- 주입 개수를 MAX_NOTES로 제한해 토큰을 통제한다.

references.md를 찾지 못하면 조용히 빈 노트로 동작한다 — 배포 환경(rootDir=backend)
에서 파일이 없더라도 죽지 않고, 없는 내용을 지어내지도 않는다.
"""
import re
from functools import lru_cache
from pathlib import Path

MAX_NOTES = 2          # 한 답변에 펼칠 상세 노트 수(토큰 방어)
MAX_NOTE_CHARS = 700   # 노트 1건 길이 상한

# 저장소 루트가 backend/의 부모다. 배포 형태가 달라질 수 있어 후보를 순회한다.
_SEARCH_PATHS = (
    Path(__file__).resolve().parent.parent.parent / "references.md",
    Path(__file__).resolve().parent.parent / "references.md",
    Path.cwd() / "references.md",
)

# "### <a id="m8"></a>[M8] 제목" 부터 다음 ### 전까지가 한 항목
_ENTRY_RE = re.compile(r"^###\s+(?:<a id=\"[^\"]*\"></a>\s*)?\[([MT]\d+)\]\s*(.*)$")

# 질문 → 펼칠 근거. 담당 코퍼스 안에서만 선택되므로 오배정 위험은 없다
# (최종 인용 검증은 chat._sanitize_citations가 한 번 더 한다).
TOPIC_KEYWORDS = {
    "M1": ["신규", "노출", "발견", "침투", "가용성", "브랜드"],
    "M2": ["브랜드", "광고", "장기", "예산"],
    "M3": ["바이럴", "공유", "입소문", "sns", "콘텐츠"],
    "M4": ["가격", "할인", "프레이밍", "손실", "객단가", "세트", "오퍼"],
    "M5": ["가격", "끝자리", "9900", "가격표", "객단가", "세트", "메뉴판", "인상"],
    "M6": ["사회적", "증거", "설득", "후기", "추천"],
    "M7": ["이탈", "리텐션", "재방문", "충성"],
    "M8": ["rfm", "clv", "ltv", "생애가치", "세분화", "멤버십", "멤버쉽", "단골", "포스"],
    "M9": ["nps", "만족", "추천지수"],
    "M10": ["충성", "수익", "세분화", "vip"],
    "M11": ["평점", "별점", "리뷰"],
    "M12": ["평점", "별점", "예약", "매진"],
    "M13": ["부정", "악평", "리뷰", "불만", "컴플레인", "클레임", "대응", "별점테러"],
    "M14": ["리뷰", "국내", "한국", "평점"],
    "M15": ["리뷰", "노출", "순위", "플레이스", "검색"],
    "M16": ["nps", "반박", "재현"],
    "M17": ["적립", "스탬프", "쿠폰", "로열티", "멤버십", "멤버쉽", "카페", "목표"],
    "M18": ["추천", "리퍼럴", "소개", "지인"],
    "M19": ["로열티", "멤버십", "멤버쉽", "적립", "인센티브"],
    "M20": ["배달", "플랫폼", "포장", "배민", "쿠팡", "요기요", "입점", "테이크아웃"],
    "M21": ["배달", "국내", "한국", "배달앱", "배민", "쿠팡", "입점"],
    "M22": ["로열티", "멤버십", "멤버쉽", "단기", "효과"],
}


@lru_cache(maxsize=1)
def _load_notes() -> dict:
    """references.md → {mid: 상세 노트}. 파일이 없으면 빈 dict(조용한 degradation)."""
    path = next((p for p in _SEARCH_PATHS if p.is_file()), None)
    if path is None:
        return {}
    notes, mid, buf = {}, None, []

    def _flush():
        if mid and buf:
            notes[mid] = "\n".join(buf).strip()[:MAX_NOTE_CHARS]

    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENTRY_RE.match(line)
        if m:
            _flush()
            mid, buf = m.group(1), [m.group(2).strip()]
        elif mid is not None:
            if line.startswith("## "):   # 섹션 경계 → 현재 항목 종료
                _flush()
                mid, buf = None, []
            elif line.strip():
                buf.append(line.rstrip())
    _flush()
    return notes


def get_note(mid: str) -> str:
    """단일 근거의 상세 노트(없으면 빈 문자열)."""
    return _load_notes().get(mid, "")


def relevant_mids(message: str, allowed_mids) -> list:
    """질문과 관련된 근거 번호를 담당 코퍼스 안에서 고른다(결정론적, 추가 API 호출 0).

    키워드 적중 수로 정렬하고 MAX_NOTES개까지. 적중이 없으면 빈 리스트 —
    억지로 아무 근거나 펼치지 않는다.
    """
    low = (message or "").lower()
    scored = []
    for mid in allowed_mids:
        hits = sum(1 for kw in TOPIC_KEYWORDS.get(mid, []) if kw in low)
        if hits:
            scored.append((hits, mid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [mid for _, mid in scored[:MAX_NOTES]]


def notes_block(message: str, allowed_mids) -> str:
    """프롬프트에 붙일 2단 근거 블록. 관련 근거가 없거나 노트가 없으면 빈 문자열."""
    picked = [(mid, get_note(mid)) for mid in relevant_mids(message, allowed_mids)]
    picked = [(mid, note) for mid, note in picked if note]
    if not picked:
        return ""
    body = "\n\n".join(f"[{mid}] {note}" for mid, note in picked)
    return ("\n\n[근거 상세 — 질문과 관련된 항목만 펼침]\n" + body +
            "\n이 상세 내용을 근거로 답하되, 여기 적힌 맥락(국가·업종·연도)을 벗기지 마라. "
            "노트에 없는 수치·표본·연구설계를 지어내지 마라.")
