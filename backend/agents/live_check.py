"""
라이브 연동 점검 스크립트 (실제 Claude 호출).

전제: ANTHROPIC_API_KEY 설정 + anthropic SDK 설치 + api.anthropic.com egress 허용.
실행:
    cd marketing-orchestra/backend
    PYTHONPATH=. ANTHROPIC_API_KEY=... python agents/live_check.py
(키는 환경변수/환경 시크릿으로만 주입 — 코드·채팅에 하드코딩 금지)
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from agents.llm import get_provider  # noqa: E402


def main() -> int:
    provider = get_provider()
    print(f"provider = {provider.name} (router={provider.router_model}, generator={provider.generator_model})")
    if provider.name == "stub":
        print("⚠️  StubProvider로 동작 중 — GEMINI_API_KEY/ANTHROPIC_API_KEY 미설정.")
        print("    환경 시크릿에 키를 넣고 세션을 재시작하세요.")
        return 1

    # 최소 비용 라우터 호출로 실연동 확인
    text, usage = provider.complete(
        model=provider.router_model,
        system="너는 마케팅 의도 분류기다. acq/cvr/ret 중 관련된 것만 콤마로 답하라.",
        prompt="신규 손님을 더 끌고 싶어요",
        max_tokens=20,
    )
    print(f"✅ 라이브 호출 성공: '{text.strip()}'")
    print(f"   usage: in={usage.input_tokens} out={usage.output_tokens} cost=${usage.cost_usd():.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
