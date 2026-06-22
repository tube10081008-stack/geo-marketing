"""
LLM 추상화 + 모델 티어링 + 크레딧 계측 (orchestrator §6·§7).

멀티 프로바이더: Gemini(REST) / Anthropic(SDK) / Stub(오프라인 결정적).
- complete(): 단발 생성(라우팅·단건 액션)
- chat():     대화이력(history) 기반 멀티턴 생성
각 프로바이더는 자기 티어 모델(router/generator)을 안다.

단가는 공개 기준(2026-06)의 근사치. 환경변수로 모델·단가 오버라이드 가능.
"""
import json
import math
import os
import urllib.request
from dataclasses import dataclass

PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "stub": (3.0, 15.0),
}
CREDIT_COST_USD = float(os.environ.get("GEO_CREDIT_COST_USD", "0.01"))


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def cost_usd(self) -> float:
        pin, pout = PRICING.get(self.model, (3.0, 15.0))
        billable_in = self.input_tokens + self.cache_read_tokens * 0.1
        return (billable_in * pin + self.output_tokens * pout) / 1_000_000


class Meter:
    def __init__(self):
        self.calls: list[Usage] = []

    def add(self, u: Usage):
        self.calls.append(u)

    def total_cost_usd(self) -> float:
        return sum(u.cost_usd() for u in self.calls)

    def credits(self) -> int:
        return max(1, math.ceil(self.total_cost_usd() / CREDIT_COST_USD))

    def breakdown(self) -> list[dict]:
        return [{"model": u.model, "in": u.input_tokens, "out": u.output_tokens,
                 "cost_usd": round(u.cost_usd(), 6)} for u in self.calls]


def _to_contents(messages):
    out = []
    for m in messages or []:
        role = "user" if m.get("role") == "user" else "model"
        out.append({"role": role, "parts": [{"text": m.get("text", "")}]})
    return out


class StubProvider:
    name = "stub"
    router_model = "stub"
    generator_model = "stub"

    def complete(self, *, model, system, prompt, max_tokens=800):
        in_tok = (len(system) + len(prompt)) // 4
        return prompt, Usage(model=model, input_tokens=in_tok, output_tokens=max(40, len(prompt) // 8))

    def chat(self, *, system, messages, max_tokens=600):
        last = (messages or [{}])[-1].get("text", "")
        text = (f"(데모/Stub 응답) '{last}'에 대해 베이스라인과 코퍼스를 근거로, "
                "시스템 지침의 [M#] 근거에 따라 측정가능한 1~2개 실행안을 권합니다. "
                "실제 생성 답변은 GEMINI_API_KEY 설정 시 제공됩니다.")
        return text, Usage(model="stub", input_tokens=len(system) // 4, output_tokens=60)


class GeminiProvider:
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
        out = um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0)
        return text, Usage(model=model, input_tokens=um.get("promptTokenCount", 0), output_tokens=out)

    def complete(self, *, model, system, prompt, max_tokens=800):
        return self._post(model, system, [{"role": "user", "parts": [{"text": prompt}]}], max_tokens)

    def chat(self, *, system, messages, max_tokens=600):
        return self._post(self.generator_model, system, _to_contents(messages), max_tokens)


class AnthropicProvider:
    name = "anthropic"
    router_model = os.environ.get("GEO_ANTHROPIC_ROUTER", "claude-haiku-4-5")
    generator_model = os.environ.get("GEO_ANTHROPIC_GENERATOR", "claude-sonnet-4-6")

    def _client(self):
        import anthropic
        return anthropic.Anthropic()

    def complete(self, *, model, system, prompt, max_tokens=800):
        resp = self._client().messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}])
        return self._text(resp)

    def chat(self, *, system, messages, max_tokens=600):
        conv = [{"role": "user" if m.get("role") == "user" else "assistant",
                 "content": m.get("text", "")} for m in messages or []]
        resp = self._client().messages.create(
            model=self.generator_model, max_tokens=max_tokens, system=system, messages=conv)
        return self._text(resp)

    def _text(self, resp):
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return text, Usage(model=resp.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                           cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)


def get_provider():
    """키 우선순위: Gemini → Anthropic → Stub."""
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider()
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
            return AnthropicProvider()
        except ImportError:
            pass
    return StubProvider()
