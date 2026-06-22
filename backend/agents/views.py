"""에이전트 API — 지오에게 베이스라인 기반 액션 요청."""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from onboarding.models import Merchant

from .orchestrator import run_geo


class AdviseView(APIView):
    """POST /api/v1/agents/advise/  {merchant_id, question?}

    지오가 라우팅→페르소나→근거인용 액션+크레딧 청구액을 반환.
    """

    def post(self, request):
        merchant_id = request.data.get("merchant_id")
        question = request.data.get("question", "")
        merchant = get_object_or_404(Merchant, pk=merchant_id)
        return Response(run_geo(merchant, question))
