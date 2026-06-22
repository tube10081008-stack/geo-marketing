"""
Vercel 서버리스: POST /api/chat — 세 에이전트와 멀티턴 대화 (self-contained).

지오가 메시지를 적합 페르소나로 라우팅(저가 모델/규칙) → 해당 페르소나가
베이스라인 + 대화이력 + [M#] 코퍼스로 답하고, 영역 밖이면 다른 에이전트로 핸드오프.
stateless: 대화이력은 프런트가 들고 매 턴 재전송(Messages API 패턴).
"""
import json
import math
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

CORPUS = {
    "acq": [("M1", "신규도달(침투)+정신적/물리적 가용성이 성장 동력", "Sharp 2010"),
            ("M11", "리뷰 평점 1점↑→매출 5~9%↑(독립업체)", "Luca 2011, HBS"),
            ("M15", "리뷰 개수·품질이 음식점 노출순위 좌우(국내)", "엄해정·진현정 2024")],
    "cvr": [("M4", "손실회피·프레이밍·기준점", "Kahneman & Tversky 1979"),
            ("M5", "9-끝자리 가격이 현장실험 수요↑", "Anderson & Simester 2003"),
            ("M12", "반 별점↑→매진 49%↑", "Anderson & Magruder 2012")],
    "ret": [("M7", "이탈률 5%p↓→이익 25~85%↑", "Reichheld & Sasser 1990"),
            ("M8", "RFM 세분화→CLV 추정", "Fader·Hardie·Lee 2005"),
            ("M13", "부정 리뷰가 더 무겁다→신속대응", "Chevalier & Mayzlin 2006")],
}
PERSONA_META = {
    "acq": ("🎣 획득", "신규유입·노출·리뷰수"),
    "cvr": ("💳 전환", "전환율·객단가·평점"),
    "ret": ("🔁 유지", "재방문율·LTV·NPS"),
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

    def chat(self, system, contents, max_tokens=600):
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

    def chat(self, system, contents, max_tokens=600):
        return self._post(self.generator_model, system, contents, max_tokens)


def get_provider():
    return Gemini() if os.environ.get("GEMINI_API_KEY") else Stub()


def _card_summary(fields):
    def g(k):
        v = fields.get(k)
        return v if v not in (None, "") else "?"
    return (f"평점 {g('avg_rating')}, 리뷰 {g('review_count')}, 플레이스클릭 {g('place_clicks')}, "
            f"객단가 {g('aov')}, 전환율 {g('visit_to_purchase_rate')}%, "
            f"재방문 {g('revisit_rate')}%, NPS {g('nps')}")


def route_chat(message, fields, forced, provider, meter):
    if forced in ("acq", "cvr", "ret"):
        return forced
    low = (message or "").lower()
    scores = {p: sum(1 for kw in kws if kw in low) for p, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    # 키워드로 못 정하면 저가 라우터 모델(있을 때)
    if provider.name != "stub":
        text, (i, o) = provider.complete(
            provider.router_model,
            "마케팅 의도 분류기. acq(획득)/cvr(전환)/ret(유지) 중 하나만 답하라.",
            message, max_tokens=8)
        meter.add(provider.router_model, i, o)
        for p in ("acq", "cvr", "ret"):
            if p in text:
                return p
    return "cvr"


def _system(persona, merchant, fields):
    label, kpi = PERSONA_META[persona]
    cites = "\n".join(f"- [{m}] {c} ({s})" for m, c, s in CORPUS[persona])
    others = ", ".join(PERSONA_META[p][0] for p in ("acq", "cvr", "ret") if p != persona)
    return (f"너는 소상공인 '{merchant}'의 마케팅 {label} 담당 에이전트다. 담당 KPI: {kpi}.\n"
            f"베이스라인: {_card_summary(fields)}.\n"
            f"근거 코퍼스(관련 시 반드시 [M#] 인용):\n{cites}\n"
            f"너는 획득·전환·유지 3인 팀의 일원이다. 네 영역 밖 질문이면 {others} 에이전트에게 넘기라고 안내하라.\n"
            "사장님과 대화하듯 간결하고 실행가능하게 답하라. 2~4문장. 출처를 댈 수 없는 단정은 금지.")


def run_chat(merchant, fields, history, message, forced=None):
    provider = get_provider()
    meter = Meter()
    persona = route_chat(message, fields, forced, provider, meter)
    label, kpi = PERSONA_META[persona]

    # 대화이력 → Gemini contents(user/model)
    contents = []
    for h in history or []:
        role = "user" if h.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h.get("text", "")}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    reply, (i, o) = provider.chat(_system(persona, merchant or "내 가게", fields), contents)
    meter.add(provider.generator_model, i, o)

    return {
        "persona": persona,
        "label": label,
        "kpi": kpi,
        "reply": reply,
        "citations": [m for m, _, _ in CORPUS[persona]],
        "billing": {"provider": provider.name, "credits_charged": meter.credits(),
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
