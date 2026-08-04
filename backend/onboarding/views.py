"""
온보딩 API 뷰.

- MerchantViewSet: 업체 + 베이스라인 통합 CRUD(중첩 폼)
    · POST /api/v1/onboarding/merchants/         첫 만남 통합 등록
    · GET  /api/v1/onboarding/merchants/{id}/card/  베이스라인 카드(산출물)
- CustomerTransactionViewSet: RFM 원천 거래로그 적재(동의 전제)
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CustomerTransaction, Merchant, record_snapshot
from .serializers import (
    BaselineCardSerializer,
    CustomerTransactionSerializer,
    MerchantIntakeSerializer,
)

# intake 폼 → 시계열 스냅샷 매핑 (관계, 필드)
_SNAPSHOT_FIELDS = [
    ("acquisition", "avg_rating"), ("acquisition", "review_count"),
    ("acquisition", "place_clicks"),
    ("conversion", "aov"), ("conversion", "monthly_customers"),
    ("conversion", "visit_to_purchase_rate"),
    ("retention", "revisit_rate"), ("retention", "regular_ratio"),
    ("retention", "nps"),
]


def _snapshot_intake(merchant):
    """데이터 입력 폼의 지표를 시계열로 축적 — 값이 그대로면 스킵."""
    if merchant.monthly_revenue is not None:
        record_snapshot(merchant, "monthly_revenue", merchant.monthly_revenue, "intake")
    for rel, field in _SNAPSHOT_FIELDS:
        base = getattr(merchant, rel, None)
        value = getattr(base, field, None) if base is not None else None
        if value is not None:
            record_snapshot(merchant, field, value, "intake")


class MerchantViewSet(viewsets.ModelViewSet):
    """계정별 워크스페이스 — 로그인 사용자는 자기 업체만 조회/생성/수정."""

    serializer_class = MerchantIntakeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Merchant.objects.filter(owner=self.request.user)
            .select_related("acquisition", "conversion", "retention")
        )

    def perform_create(self, serializer):
        merchant = serializer.save(owner=self.request.user)
        _snapshot_intake(merchant)

    def perform_update(self, serializer):
        merchant = serializer.save()
        _snapshot_intake(merchant)

    @action(detail=True, methods=["get"])
    def card(self, request, pk=None):
        """베이스라인 카드 — 첫 만남 산출물(KPI 스냅샷 + 데이터등급)."""
        merchant = self.get_object()
        return Response(BaselineCardSerializer(merchant).data)


class CustomerTransactionViewSet(viewsets.ModelViewSet):
    """RFM 원천 거래로그 — 가명 키이지만 고객 행동 데이터다. 소유자 외 접근 금지.

    ※ 과거 결함: permission_classes와 get_queryset이 없어 전역 기본값(AllowAny)이
      적용됐고, 익명 요청으로 **전체 가맹점의 거래로그가 조회**됐다. 남의 업체에
      거래를 주입하는 것도 가능했다. 아래 두 겹으로 막는다.
    """

    serializer_class = CustomerTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # 소유자 스코프 — 남의 업체 거래로그는 조회·수정·삭제 모두 불가
        return (CustomerTransaction.objects
                .filter(merchant__owner=self.request.user)
                .select_related("merchant"))

    def _owned_merchant(self, merchant_id):
        """요청자가 소유한 업체만 반환. 아니면 None(존재 여부도 알려주지 않는다)."""
        return Merchant.objects.filter(
            pk=merchant_id, owner=self.request.user).first()

    def create(self, request, *args, **kwargs):
        """적재 전 소유권 + 위탁처리 동의 확인(intake §4 개인정보 게이트)."""
        merchant = self._owned_merchant(request.data.get("merchant"))
        if merchant is None:
            return Response(
                {"detail": "존재하지 않거나 접근 권한이 없는 업체입니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not merchant.consent_data_processing:
            return Response(
                {"detail": "고객데이터 위탁처리 동의가 필요합니다(intake §4)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """merchant를 남의 업체로 바꿔치기하는 경로도 막는다."""
        target = request.data.get("merchant")
        if target is not None and self._owned_merchant(target) is None:
            return Response(
                {"detail": "존재하지 않거나 접근 권한이 없는 업체입니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return super().update(request, *args, **kwargs)
