"""
에이전트 API — 계정 보호 + 대화 영속.

- AdviseView : 베이스라인 → 1회 종합 액션 (자기 업체만)
- ChatView   : 세 에이전트와 대화. 사용자/에이전트 메시지를 DB에 저장(세션 지속).
- HistoryView: 업체 대화 이력 조회(새 기기/재접속에도 복원).
"""
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from onboarding.models import ChatMessage, Merchant

from .chat import run_chat
from .orchestrator import run_geo


def _merchant(request, merchant_id):
    """소유자 스코프 — 남의 업체 접근 차단."""
    return get_object_or_404(Merchant, pk=merchant_id, owner=request.user)


class AdviseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        return Response(run_geo(merchant, request.data.get("question", "")))


class ChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        merchant = _merchant(request, request.data.get("merchant_id"))
        message = (request.data.get("message") or "").strip()
        forced = request.data.get("persona") or None
        if not message:
            return Response({"detail": "메시지가 비어 있습니다."}, status=400)

        # 저장된 이력을 LLM 맥락으로(시스템 메시지는 제외)
        history = [
            {"role": m.role, "text": m.text}
            for m in merchant.messages.exclude(role=ChatMessage.Role.SYSTEM)
        ]
        result = run_chat(merchant, history, message, forced)

        # 영속: 사용자 메시지 + (갱신 시)시스템 + 에이전트 답변들
        ChatMessage.objects.create(merchant=merchant, role="user", text=message)
        if result.get("updated_fields"):
            ChatMessage.objects.create(
                merchant=merchant, role="system",
                text="베이스라인 갱신: " + ", ".join(
                    f"{k} {v}" for k, v in result["updated_fields"].items()),
            )
        for t in result["turns"]:
            ChatMessage.objects.create(
                merchant=merchant, role="agent", persona=t["persona"], text=t["reply"])

        return Response(result)


class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, merchant_id):
        merchant = _merchant(request, merchant_id)
        msgs = [
            {"role": m.role, "persona": m.persona, "text": m.text,
             "created_at": m.created_at.isoformat()}
            for m in merchant.messages.all()
        ]
        return Response({"merchant": merchant.name, "messages": msgs})
