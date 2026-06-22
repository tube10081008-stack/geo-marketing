"""
온보딩 API 뷰.

- MerchantViewSet: 업체 + 베이스라인 통합 CRUD(중첩 폼)
    · POST /api/v1/onboarding/merchants/         첫 만남 통합 등록
    · GET  /api/v1/onboarding/merchants/{id}/card/  베이스라인 카드(산출물)
- CustomerTransactionViewSet: RFM 원천 거래로그 적재(동의 전제)
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import CustomerTransaction, Merchant
from .serializers import (
    BaselineCardSerializer,
    CustomerTransactionSerializer,
    MerchantIntakeSerializer,
)


class MerchantViewSet(viewsets.ModelViewSet):
    queryset = Merchant.objects.all().select_related(
        "acquisition", "conversion", "retention"
    )
    serializer_class = MerchantIntakeSerializer

    @action(detail=True, methods=["get"])
    def card(self, request, pk=None):
        """베이스라인 카드 — 첫 만남 산출물(KPI 스냅샷 + 데이터등급)."""
        merchant = self.get_object()
        return Response(BaselineCardSerializer(merchant).data)


class CustomerTransactionViewSet(viewsets.ModelViewSet):
    queryset = CustomerTransaction.objects.select_related("merchant")
    serializer_class = CustomerTransactionSerializer

    def create(self, request, *args, **kwargs):
        """거래로그 적재 전 위탁처리 동의 확인(intake §4 개인정보 게이트)."""
        merchant_id = request.data.get("merchant")
        merchant = Merchant.objects.filter(pk=merchant_id).first()
        if merchant and not merchant.consent_data_processing:
            return Response(
                {"detail": "고객데이터 위탁처리 동의가 필요합니다(intake §4)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)
