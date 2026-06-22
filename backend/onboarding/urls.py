"""온보딩 API 라우팅."""
from rest_framework.routers import DefaultRouter

from .views import CustomerTransactionViewSet, MerchantViewSet

router = DefaultRouter()
router.register("onboarding/merchants", MerchantViewSet, basename="merchant")
router.register(
    "onboarding/transactions", CustomerTransactionViewSet, basename="transaction"
)

urlpatterns = router.urls
