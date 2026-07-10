"""
Vercel Python 서버리스 함수: POST /api/advise  (단일 파일 self-contained).

형제 모듈 임포트(_geo)는 Vercel 번들/경로에서 실패할 수 있어 로직을 인라인했다.
코퍼스+게이트라우팅+페르소나+Gemini(stdlib only). GEMINI_API_KEY 있으면 실연동.
"""
import json
import math
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

# ── 코퍼스 [M1]~[M15] (references.md 교차검증) ──────────────────────
# 문구 원칙(REVIEW.md): 원 연구의 맥락(국가·업태)을 벗기지 않는다 — 수치는 약속이 아니라 방향성 근거.
CORPUS = {
    "acq": [
        ("M1", "매출성장 주동력은 신규도달(침투)+가용성", "Sharp 2010, How Brands Grow"),
        ("M11", "평점 1점↑→매출 5~9%↑(美 독립식당, 방향성 근거)", "Luca 2011, HBS WP"),
        ("M15", "리뷰 개수·품질평가가 노출순위 좌우(국내 외식)", "엄해정·진현정 2024, 호텔경영학연구"),
    ],
    "cvr": [
        ("M4", "손실회피·프레이밍(오퍼 카피는 A/B 검증 후 채택)", "Kahneman & Tversky 1979, Econometrica"),
        ("M5", "9-끝자리 수요↑(美 통판, 세일 병용 시 약화)", "Anderson & Simester 2003, QME"),
        ("M12", "반 별점↑→예약매진 +19%p(상대 49%, 美)", "Anderson & Magruder 2012, Economic Journal"),
    ],
    "ret": [
        ("M7", "이탈률↓→이익↑(1990 산업별 사례 25~85%, 일반화 금지)", "Reichheld & Sasser 1990, HBR"),
        ("M8", "RFM 세분화 → CLV 추정(경량)", "Fader·Hardie·Lee 2005, JMR"),
        ("M13", "부정 리뷰가 더 무겁다 → 신속대응", "Chevalier & Mayzlin 2006, JMR"),
        ("M10", "충성≠수익 — 수익성 세그먼트 우선", "Reinartz & Kumar 2002, HBR"),
        ("M16", "(반론) NPS 우월성 재현 실패 — 보조지표로만", "Keiningham et al. 2007, J. of Marketing"),
    ],
}
PRICING = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "stub": (3.0, 15.0),
}
CREDIT_COST_USD = float(os.environ.get("GEO_CREDIT_COST_USD", "0.01"))
PERSONA_META = {
    "acq": ("🎣 획득", "신규유입·노출·리뷰수"),
    "cvr": ("💳 전환", "전환율·객단가·평점"),
    "ret": ("🔁 유지", "재방문율·LTV·NPS"),
}


class Meter:
    def __init__(self):
        self.calls = []

    def add(self, model, itok, otok):
        self.calls.append((model, itok, otok))

    def cost(self):
        total = 0.0
        for model, itok, otok in self.calls:
            pin, pout = PRICING.get(model, (3.0, 15.0))
            total += (itok * pin + otok * pout) / 1_000_000
        return total

    def credits(self):
        return max(1, math.ceil(self.cost() / CREDIT_COST_USD))

    def breakdown(self):
        return [{"model": m, "in": i, "out": o} for m, i, o in self.calls]


class Stub:
    name = "stub"
    router_model = "stub"
    generator_model = "stub"

    def complete(self, model, system, prompt, max_tokens=900):
        return prompt, (len(system + prompt) // 4, max(40, len(prompt) // 8))


class Gemini:
    name = "gemini"
    router_model = os.environ.get("GEO_GEMINI_ROUTER", "gemini-2.5-flash-lite")
    generator_model = os.environ.get("GEO_GEMINI_GENERATOR", "gemini-2.5-flash")
    _EP = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"

    def complete(self, model, system, prompt, max_tokens=900):
        key = os.environ["GEMINI_API_KEY"]
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens,
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        req = urllib.request.Request(
            self._EP.format(m=model, k=key), data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        um = data.get("usageMetadata", {})
        out = um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0)
        return text, (um.get("promptTokenCount", 0), out)


def get_provider():
    return Gemini() if os.environ.get("GEMINI_API_KEY") else Stub()


def build_card(merchant, fields):
    def num(k):
        v = fields.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    grades = [fields.get("acq_grade", "C"), fields.get("cvr_grade", "C"), fields.get("ret_grade", "C")]
    worst = max(grades, key=["A", "B", "C", "D"].index)
    return {
        "merchant": merchant,
        "overall_data_grade": worst,
        "acquisition": {"avg_rating": num("avg_rating"), "review_count": num("review_count"),
                        "monthly_traffic_proxy": num("place_clicks"), "grade": fields.get("acq_grade", "C")},
        "conversion": {"aov_krw": num("aov"), "visit_to_purchase_rate_pct": num("visit_to_purchase_rate"),
                       "grade": fields.get("cvr_grade", "C")},
        "retention": {"revisit_rate_pct": num("revisit_rate"), "nps": num("nps"),
                      "grade": fields.get("ret_grade", "C")},
        "disclaimer": "투영치는 베이스라인 기반 추정이며 인과 보장이 아님(orchestrator §4.3).",
    }


def route(card):
    # ⚠️ 휴리스틱 v1(REVIEW): 임계값은 업종 벤치마크 없는 잠정치 — "개선 여지" 신호로만 사용
    acq, cvr, ret = card["acquisition"], card["conversion"], card["retention"]
    woke = {}
    r = acq.get("avg_rating")
    if r is None or r < 4.3:
        woke["acq"] = f"평점·노출에 개선 여지(rating={r}) → 발견·리뷰 보강"
    c = cvr.get("visit_to_purchase_rate_pct")
    if c is None or c < 60:
        woke["cvr"] = f"전환율에 개선 여지(conv={c}%) → 평점·오퍼 최적화"
    elif r is None or r < 4.3:
        woke.setdefault("cvr", "평점이 전환에 직결 → 함께 점검")
    rv, n = ret.get("revisit_rate_pct"), ret.get("nps")
    if rv is None or rv < 35 or (n is not None and n < 30):
        woke["ret"] = f"재방문·NPS에 개선 여지(revisit={rv}%, nps={n}) → 리텐션"
    if not woke:
        woke["cvr"] = "뚜렷한 개선 신호 없음 → 전환 미세최적화로 quick-win"
    return woke


def _draft(persona, card):
    acq, cvr, ret = card["acquisition"], card["conversion"], card["retention"]
    if persona == "acq":
        r = acq.get("avg_rating") or 4.0
        return (f"네이버 플레이스 노출·리뷰 보강 + 만족고객 영수증 리뷰 요청. "
                f"목표: 평점 {r}→{round(r+0.2,1)}, 리뷰수 +20%(4주). "
                f"근거: 평점↑→매출↑(美 독립식당 5~9%, 방향성)[M11], 리뷰수가 노출순위 좌우(국내)[M15], 신규도달=성장[M1]."), ["M11", "M15", "M1"]
    if persona == "cvr":
        c = cvr.get("visit_to_purchase_rate_pct") or 55
        a = cvr.get("aov_krw") or 0
        return (f"평점 0.1~0.2 상향 + '오늘만' 손실프레임 오퍼 A/B + 가격 끝자리 9. "
                f"목표: 전환율 {c}%→{round(c+3,1)}%, 객단가 {int(a)}원 유지. "
                f"근거: 반별점↑→예약매진 +19%p(美)[M12], 9-끝자리 수요↑(세일 병용 시 약화)[M5], 손실회피(A/B 검증 전제)[M4]."), ["M12", "M5", "M4"]
    rv = ret.get("revisit_rate_pct") or 30
    n = ret.get("nps") or 30
    return (f"RFM 단골 세분화→이탈임박 세그먼트 재방문 쿠폰(수익성 세그먼트 우선[M10]) + 부정리뷰 24h 응대룰. "
            f"목표: 재방문율 {rv}%→{round(rv+5,1)}%, NPS {int(n)}→{int(n)+5}(보조지표). "
            f"근거: 이탈↓→이익↑(산업별 사례)[M7], RFM·CLV[M8], 부정리뷰 신속대응[M13], NPS는 보조지표[M16]."), ["M7", "M8", "M13", "M10", "M16"]


def advise(persona, card, provider, meter):
    draft, mids = _draft(persona, card)
    label, kpi = PERSONA_META[persona]
    cite = "\n".join(f"- [{m}] {claim} ({src})"
                     for m, claim, src in CORPUS[persona] if m in mids)
    system = (f"너는 소상공인 마케팅 {label} 전문가다. 담당 KPI: {kpi}. "
              "반드시 [M#]를 인용하고 측정가능한 목표를 제시하라. 출처 없는 단정 금지.")
    prompt = (f"[베이스라인]\n{json.dumps(card, ensure_ascii=False)}\n\n[근거]\n{cite}\n\n"
              f"[초안]\n{draft}\n\n초안을 사장님이 바로 실행할 1~2개 액션으로 다듬어라.")
    text, (itok, otok) = provider.complete(provider.generator_model, system, prompt)
    meter.add(provider.generator_model, itok, otok)
    surfaced = draft if provider.name == "stub" else (text or draft)
    return {"persona": persona, "label": label, "kpi": kpi, "action": surfaced,
            "citations": mids, "generator_model": provider.generator_model}


def run_geo(merchant, fields, question=""):
    card = build_card(merchant or "내 가게", fields)
    provider = get_provider()
    meter = Meter()
    reasons = route(card)
    if question and provider.name != "stub":
        text, (itok, otok) = provider.complete(
            provider.router_model,
            "너는 마케팅 의도 분류기다. acq/cvr/ret 중 관련된 것만 콤마로 답하라.",
            question, max_tokens=20)
        meter.add(provider.router_model, itok, otok)
        for k in ("acq", "cvr", "ret"):
            if k in text:
                reasons.setdefault(k, f"고객 질의 의도({k})")
    actions = [advise(p, card, provider, meter) for p in reasons]
    return {
        "merchant": card["merchant"],
        "overall_data_grade": card["overall_data_grade"],
        "routing": {"woke_personas": list(reasons), "reasons": reasons,
                    "router": provider.router_model if question and provider.name != "stub" else "rule-based(무료)"},
        "actions": actions,
        "billing": {"provider": provider.name,
                    "credits_charged": 0 if provider.name == "stub" else meter.credits(),
                    "raw_cost_usd": round(meter.cost(), 6), "calls": meter.breakdown(),
                    "note": "계측=과금: 원가≤청구로 마진 보호. 데모(stub)는 미청구."},
        "disclaimer": card["disclaimer"],
    }


# ── Vercel 핸들러 ───────────────────────────────────────────────────
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
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = run_geo(payload.get("merchant", ""), payload.get("fields", {}),
                             payload.get("question", ""))
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
