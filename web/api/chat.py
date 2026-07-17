"""
Vercel 서버리스: POST /api/chat — 세 에이전트와 멀티턴 대화 (self-contained).

복어(Fugu)가 메시지를 적합 페르소나로 라우팅(저가 모델/규칙) → 해당 페르소나가
베이스라인 + 대화이력 + [M#] 코퍼스로 답하고, 영역 밖이면 다른 에이전트로 핸드오프.
stateless: 대화이력은 프런트가 들고 매 턴 재전송(Messages API 패턴).
"""
import json
import math
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler

# 문구 원칙(REVIEW.md): 원 연구 맥락(국가·업태)을 벗기지 않는다 — 수치는 방향성 근거.
CORPUS = {
    "acq": [("M1", "매출성장 주동력은 신규도달(침투)+가용성", "Sharp 2010"),
            ("M11", "평점 1점↑→매출 5~9%↑(美 독립식당, 방향성 근거)", "Luca 2011, HBS"),
            ("M15", "리뷰 개수·품질이 노출순위 좌우(국내 외식)", "엄해정·진현정 2024")],
    "cvr": [("M4", "손실회피·프레이밍(카피는 A/B 검증 후 채택)", "Kahneman & Tversky 1979"),
            ("M5", "9-끝자리 수요↑(美 통판, 세일 병용 시 약화)", "Anderson & Simester 2003"),
            ("M12", "반 별점↑→예약매진 +19%p(美)", "Anderson & Magruder 2012")],
    "ret": [("M7", "이탈률↓→이익↑(산업별 사례 25~85%, 일반화 금지)", "Reichheld & Sasser 1990"),
            ("M8", "RFM 세분화→CLV 추정", "Fader·Hardie·Lee 2005"),
            ("M13", "부정 리뷰가 더 무겁다→신속대응", "Chevalier & Mayzlin 2006"),
            ("M10", "충성≠수익 — 수익성 세그먼트 우선", "Reinartz & Kumar 2002"),
            ("M16", "(반론) NPS 우월성 재현 실패 — 보조지표로만", "Keiningham et al. 2007")],
    # 🧾 세무(Cora) — 법령·국세청 근거(2026-07 교차검증), 세무대리 아님
    "cora": [("T1", "부가세 확정신고: 일반 연2회(7/25·익년1/25)·간이 연1회(익년1/25)", "부가가치세법 §48·§49"),
             ("T2", "간이과세 기준: 직전연도 공급대가 1억400만원 미만(2024.7.~), 4,800만원 미만 납부면제", "부가세법 시행령 §109(2026 확인)"),
             ("T3", "종합소득세 신고: 다음해 5/1~5/31(성실신고확인 대상 6/30)", "소득세법 §70·§70의2"),
             ("T6", "노란우산공제 소득공제 — 소득구간별 연 최대 600만원(2025년 납입분부터)", "조특법 §86의3(2026 확인)"),
             ("T7", "신용카드·현금영수증 발행세액공제 1.3%, 연 1,000만원 한도(2026.12.31.까지)", "부가세법 §46(2026 확인)")],
}
PERSONA_META = {
    "acq": ("🎣 획득", "신규유입·노출·리뷰수"),
    "cvr": ("💳 전환", "전환율·객단가·평점"),
    "ret": ("🔁 유지", "재방문율·LTV·NPS"),
    "cora": ("🧾 코라 Cora", "절세·세무 일정·증빙 준비"),
}
KEYWORDS = {
    "acq": ["신규", "노출", "리뷰", "유입", "광고", "검색", "플레이스", "손님", "홍보", "인스타", "sns"],
    "cvr": ["전환", "객단가", "가격", "메뉴", "오퍼", "할인", "구매", "프로모션", "세트"],
    "ret": ["재방문", "단골", "멤버십", "쿠폰", "이탈", "nps", "리텐션", "적립"],
}
PRICING = {"gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40), "stub": (3.0, 15.0)}
CREDIT_COST_USD = float(os.environ.get("GEO_CREDIT_COST_USD", "0.01"))


class Meter:
    def __init__(self):
        self.calls = []

    def add(self, m, i, o):
        self.calls.append((m, i, o))

    def cost(self):
        return sum((i * PRICING.get(m, (3, 15))[0] + o * PRICING.get(m, (3, 15))[1]) / 1_000_000
                   for m, i, o in self.calls)

    def credits(self):
        return max(1, math.ceil(self.cost() / CREDIT_COST_USD))


class Stub:
    name = "stub"
    router_model = "stub"
    generator_model = "stub"

    def complete(self, model, system, prompt, max_tokens=40):
        return prompt, (len((system + prompt)) // 4, 8)

    def chat(self, system, contents, max_tokens=1024):
        # 결정적: 마지막 사용자 메시지 + 페르소나 첫 인용으로 grounded 응답
        last = contents[-1]["parts"][0]["text"] if contents else ""
        return (f"(데모/Stub 응답) '{last}'에 대해 베이스라인과 코퍼스를 근거로 보면, "
                f"위 시스템 지침의 [M#] 근거를 따라 측정가능한 1~2개 실행안을 권합니다. "
                f"실제 생성 답변은 GEMINI_API_KEY 설정 시 제공됩니다."), (len(system) // 4, 60)


class Gemini:
    name = "gemini"
    router_model = os.environ.get("GEO_GEMINI_ROUTER", "gemini-2.5-flash-lite")
    generator_model = os.environ.get("GEO_GEMINI_GENERATOR", "gemini-2.5-flash")
    _EP = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"

    def _post(self, model, system, contents, max_tokens):
        key = os.environ["GEMINI_API_KEY"]
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        req = urllib.request.Request(self._EP.format(m=model, k=key), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        um = data.get("usageMetadata", {})
        return text, (um.get("promptTokenCount", 0),
                      um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0))

    def complete(self, model, system, prompt, max_tokens=40):
        return self._post(model, system, [{"role": "user", "parts": [{"text": prompt}]}], max_tokens)

    def chat(self, system, contents, max_tokens=1024):
        return self._post(self.generator_model, system, contents, max_tokens)


def get_provider():
    return Gemini() if os.environ.get("GEMINI_API_KEY") else Stub()


def _card_summary(fields):
    def g(k):
        v = fields.get(k)
        return v if v not in (None, "") else "?"
    return (f"평점 {g('avg_rating')}, 리뷰 {g('review_count')}, 플레이스클릭 {g('place_clicks')}, "
            f"객단가 {g('aov')}, 월고객수 {g('monthly_customers')}, "
            f"재방문 {g('revisit_rate')}%, 단골비중 {g('regular_ratio')}%")


_CORA_RE = re.compile(
    r"코라|cora|세금|세무|부가세|부가가치세|종소세|종합소득세|절세|세액공제|소득공제|"
    r"세금계산서|현금영수증|기장|장부|간이과세|일반과세|노란우산|가산세|원천세|홈택스|필요경비|증빙", re.I)


def route_chat(message, fields, forced, provider, meter):
    """깨울 페르소나 리스트(1~2). 여러 영역에 걸친 질문이면 협업(2인)."""
    if forced in ("acq", "cvr", "ret", "cora"):
        return [forced]
    low = (message or "").lower()
    if _CORA_RE.search(low):
        return ["cora"]  # 세무 질문은 코라 단독
    scores = {p: sum(1 for kw in kws if kw in low) for p, kws in KEYWORDS.items()}
    ranked = [p for p in sorted(scores, key=lambda x: scores[x], reverse=True) if scores[p] > 0]
    if len(ranked) >= 2:
        return ranked[:2]          # 협업: 상위 2 페르소나
    if len(ranked) == 1:
        return ranked
    # 키워드 0 → 저가 라우터 모델(있을 때) / 기본 전환
    if provider.name != "stub":
        text, (i, o) = provider.complete(
            provider.router_model,
            "마케팅 의도 분류기. acq(획득)/cvr(전환)/ret(유지) 중 관련된 것만 콤마로 답하라.",
            message, max_tokens=12)
        meter.add(provider.router_model, i, o)
        picks = [p for p in ("acq", "cvr", "ret") if p in text]
        if picks:
            return picks[:2]
    return ["cvr"]


# 대화 중 지표 변경 감지 → 베이스라인 갱신(무료 정규식, 휴리스틱)
_PATTERNS = {
    "avg_rating": r"평점\s*(?:은|이|:|을)?\s*([0-5](?:\.\d)?)",
    "visit_to_purchase_rate": r"전환(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "revisit_rate": r"재방문(?:율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "review_count": r"리뷰\s*(?:가|는|를|:)?\s*(\d{2,6})\s*(?:개|건)?",
    "aov": r"객단가\s*(?:은|는|이|:)?\s*([\d,]{4,})",
    "nps": r"(?i)nps\s*(?:은|는|이|:)?\s*(-?\d{1,3})",
    "place_clicks": r"클릭\s*(?:은|는|이|:)?\s*(\d{2,7})",
    "regular_ratio": r"단골\s*(?:비중|비율)?\s*(?:은|이|:)?\s*(\d{1,3})\s*%?",
    "monthly_customers": r"(?:월\s*)?고객\s*(?:수)?\s*(?:는|은|가|:)?\s*([\d,]{2,7})\s*명",
}


# 목표/희망 발화("평점을 4.5로 올리고 싶어요")를 사실로 오인하지 않는 의도어 가드(REVIEW)
_INTENT_RE = re.compile(r"싶|려면|할까|목표|어떻게|되고 싶")
# 비용·공격 방어: 서버가 신뢰 경계 — 프런트가 보낸 값은 절단
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


def _system(persona, merchant, fields, collab=None):
    label, kpi = PERSONA_META[persona]
    cites = "\n".join(f"- [{m}] {c} ({s})" for m, c, s in CORPUS[persona])
    if persona == "cora":
        return (f"너는 🧾 코라(Cora), 소상공인 '{merchant}'의 세무 가이드 에이전트다.\n"
                "역할: 한국 세법 개념 설명·세무 일정 안내·절세 제도 소개·신고 전 준비 체크리스트.\n"
                f"검증된 근거(관련 시 반드시 [T#] 인용):\n{cites}\n"
                "[법적 경계 — 세무사법 준수] 너는 세무사가 아니다. 세무대리(신고 대행·개별 세액 확정·불복)는 "
                "할 수 없으며 하겠다고 말해서도 안 된다. 확정 판단은 세무사(무료 '마을세무사' 포함)나 "
                "국세청 126 상담을 안내하라.\n"
                "[개정 주의] 세법은 매년 바뀐다. 수치·기한엔 기준연도를 붙이고, 근거에 없는 수치는 "
                "'홈택스/세무사 확인 필요'라고 명시하라. 마케팅 질문은 획득·전환·유지 팀으로 안내. 2~5문장.")
    others = ", ".join(PERSONA_META[p][0] for p in ("acq", "cvr", "ret") if p != persona)
    base = (f"너는 소상공인 '{merchant}'의 마케팅 {label} 담당 에이전트다. 담당 KPI: {kpi}.\n"
            f"베이스라인: {_card_summary(fields)}.\n"
            f"근거 코퍼스(관련 시 반드시 [M#] 인용):\n{cites}\n"
            f"너는 획득·전환·유지 3인 팀의 일원이다. 네 영역 밖 질문이면 {others} 에이전트에게 넘기라고 안내하라.\n"
            "사장님과 대화하듯 간결하고 실행가능하게 답하라. 2~4문장. 출처를 댈 수 없는 단정은 금지.")
    if collab:
        base += (f"\n\n[협업] 동료 에이전트가 먼저 이렇게 답했다:\n\"{collab}\"\n"
                 "이를 참조하되 중복은 피하고, 네 전문영역 관점에서 보완·연결하라(시너지 한 줄).")
    return base


def run_chat(merchant, fields, history, message, forced=None):
    provider = get_provider()
    meter = Meter()
    fields = dict(fields or {})
    message = (message or "")[:MAX_MESSAGE_CHARS]
    updates = extract_updates(message)
    fields.update(updates)  # 갱신된 베이스라인으로 답변 맥락 구성

    personas = route_chat(message, fields, forced, provider, meter)

    # 대화이력 → contents(user/model). 최근 N개·글자수 절단(비용 방어)
    base_contents = []
    for h in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = "user" if h.get("role") == "user" else "model"
        base_contents.append({"role": role, "parts": [{"text": (h.get("text") or "")[:MAX_MESSAGE_CHARS]}]})
    base_contents.append({"role": "user", "parts": [{"text": message}]})

    turns = []
    collab = None
    for p in personas:
        label, kpi = PERSONA_META[p]
        system = _system(p, merchant or "내 가게", fields, collab)
        # 코라는 체크리스트형 답이 길다 — 잘림 방지로 상한 2배
        reply, (i, o) = provider.chat(system, base_contents,
                                      max_tokens=2048 if p == "cora" else 1024)
        meter.add(provider.generator_model, i, o)
        turns.append({"persona": p, "label": label, "kpi": kpi, "reply": reply,
                      "citations": [m for m, _, _ in CORPUS[p]]})
        collab = f"{label}: {reply}"  # 다음 페르소나가 참조

    return {
        "turns": turns,
        "updated_fields": updates,
        "billing": {"provider": provider.name,
                    "credits_charged": 0 if provider.name == "stub" else meter.credits(),
                    "raw_cost_usd": round(meter.cost(), 6)},
    }


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length", 0))
            p = json.loads(self.rfile.read(length) or b"{}")
            result = run_chat(p.get("merchant", ""), p.get("fields", {}),
                              p.get("history", []), p.get("message", ""), p.get("persona"))
            status = 200
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
            status = 500
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)
