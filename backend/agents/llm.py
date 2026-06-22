"""
LLM 추상화 + 모델 티어링 + 크레딧 계측 (orchestrator §6·§7).

멀티 프로바이더: Gemini(REST) / Anthropic(SDK) / Stub(오프라인 결정적).
각 프로바이더는 자기 티어 모델(router/generator)을 안다 → 호출부는 provider에 위임.

설계 원칙(저렴한 API):
- 모델 티어링: 라우팅=최저가 / 심층생성=중간가
- 계측=과금: 호출 토큰 → 원가(USD) → 크레딧 환산(원가≤청구, 마진 보호)
- 무키/무네트워크에서도 동작(StubProvider)

단가는 공개 기준(2026-06)의 근사치. 환경변수로 모델·단가 오버라이드 가능.
"""
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# 모델별 (입력, 출력) USD / 1M tokens
PRICING = {
    # Anthropic
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    # Google Gemini (근사치)
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "stub": (3.0, 15.0),
}

# 1 크레딧이 대응하는 '원가' USD. 판매가는 이 위에 마진을 얹어 책정(§7).
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
    """호출 누적 계측 → 크레딧 환산(계측=과금)."""

    def __init__(self):
        self.calls: list[Usage] = []

    def add(self, u: Usage):
        self.calls.append(u)

    def total_cost_usd(self) -> float:
        return sum(u.cost_usd() for u in self.calls)

    def credits(self) -> int:
        return max(1, math.ceil(self.total_cost_usd() / CREDIT_COST_USD))

    def breakdown(self) -> list[dict]:
        return [
            {"model": u.model, "in": u.input_tokens, "out": u.output_tokens,
             "cost_usd": round(u.cost_usd(), 6)}
            for u in self.calls
        ]


class StubProvider:
    """무키/무네트워크용 결정적 제공자. 생성 phrasing은 호출부 템플릿이 담당."""

    name = "stub"
    router_model = "stub"
    generator_model = "stub"

    def complete(self, *, model, system, prompt, max_tokens=800):
        in_tok = (len(system) + len(prompt)) // 4
        out_tok = min(max_tokens, max(40, len(prompt) // 8))
        return prompt, Usage(model=model, input_tokens=in_tok, output_tokens=out_tok)


class GeminiProvider:
    """Google Gemini REST 연동(추가 SDK 불필요). GEMINI_API_KEY 환경변수 필요."""

    name = "gemini"
    router_model = os.environ.get("GEO_GEMINI_ROUTER", "gemini-2.5-flash-lite")
    generator_model = os.environ.get("GEO_GEMINI_GENERATOR", "gemini-2.5-flash")
    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

    def complete(self, *, model, system, prompt, max_tokens=800):
        key = os.environ["GEMINI_API_KEY"]
        url = self._ENDPOINT.format(model=model, key=key)
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # thinking 비활성(thinkingBudget=0): 템플릿 다듬기엔 불필요 →
            # 출력 토큰 예산을 답변에 온전히 쓰고 비용·지연을 줄임(저렴한 API).
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        um = data.get("usageMetadata", {})
        out = um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0)
        return text, Usage(model=model, input_tokens=um.get("promptTokenCount", 0), output_tokens=out)


class AnthropicProvider:
    """Claude 연동. ANTHROPIC_API_KEY + anthropic SDK 필요."""

    name = "anthropic"
    router_model = os.environ.get("GEO_ANTHROPIC_ROUTER", "claude-haiku-4-5")
    generator_model = os.environ.get("GEO_ANTHROPIC_GENERATOR", "claude-sonnet-4-6")

    def complete(self, *, model, system, prompt, max_tokens=800):
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return text, Usage(
            model=model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )


def get_provider():
    """키 우선순위: Gemini → Anthropic → Stub. 결정은 런타임 환경에 위임(투명 보고)."""
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider()
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
            return AnthropicProvider()
        except ImportError:
            pass
    return StubProvider()
