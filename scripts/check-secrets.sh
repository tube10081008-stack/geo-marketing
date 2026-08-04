#!/usr/bin/env bash
# 커밋 전 API 키 누출 점검. 실패 시 커밋하지 말 것.
#   사용: ./scripts/check-secrets.sh
#
# 원칙(프로젝트 1번 규칙): API 키는 코드·깃·채팅 어디에도 넣지 않는다.
# 키는 Render/Vercel 환경변수에만 둔다(render.yaml은 sync:false).
#
# 패턴을 넓혀 온 이력:
# - AIza…      : Google/Gemini 전통 형식
# - sk-ant-…   : Anthropic
# - AQ.…       : Google 신형 토큰 형식(2026-08 실사용 확인 — 기존 AIza 정규식에 안 걸렸다)
# - ghp_/gho_… : GitHub 토큰
# - postgres://user:pw@… : DB 접속 문자열(암호 포함) — 2026-08 실수로 채팅에 붙은 사례 반영.
#   DATABASE_URL 은 fly secrets / 대시보드에만 두고 저장소엔 자리표시자만 남긴다.
set -uo pipefail

PATTERN='AIza[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|postgres(ql)?://[^:/@<>[:space:]]+:[^@<>[:space:]]+@'

cd "$(dirname "$0")/.."

# 추적 중인 파일만 검사(.venv 등 미추적물 제외). 이 스크립트 자신은 패턴 예시를 담고 있어 제외.
if git grep -nIE "$PATTERN" -- . ':(exclude)scripts/check-secrets.sh'; then
  echo
  echo "❌ 위 위치에서 API 키로 보이는 문자열이 발견되었습니다. 커밋하지 마세요."
  echo "   조치: 값을 제거하고 환경변수로 옮긴 뒤, 이미 커밋했다면 키를 즉시 폐기·재발급하세요."
  exit 1
fi

echo "✅ 키 누출 없음 (검사 패턴: AIza / sk-ant- / AQ. / gh*_ / postgres URL)"
