"""
포인트 지갑 — 토큰 사용량 기반 차감(젠스파크식).

- 가입 시 무료 SIGNUP_BONUS_POINTS 지급(기본 10,000).
- 1포인트 = $0.0001 원가(POINTS_PER_USD=10,000) → 실사용 토큰이 곧 차감량.
  예: 대화 1턴 ≈ $0.005 ≈ 50포인트 / 딥리포트 ≈ $0.15 ≈ 1,500포인트.
- 데모(stub) 호출은 0포인트. 마지막 호출로 잔액이 소폭 음수가 될 수 있음(허용).
"""
import math
import os

from django.conf import settings
from django.db import models

SIGNUP_BONUS_POINTS = int(os.environ.get("GEO_SIGNUP_POINTS", "10000"))
POINTS_PER_USD = 10_000


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet",
        verbose_name="사용자",
    )
    balance = models.IntegerField("잔액(포인트)", default=SIGNUP_BONUS_POINTS)
    updated_at = models.DateTimeField("갱신", auto_now=True)

    class Meta:
        verbose_name = "포인트 지갑"
        verbose_name_plural = "포인트 지갑"

    def __str__(self):
        return f"{self.user.username}: {self.balance}p"


def get_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def charge(user, cost_usd: float) -> tuple[int, int]:
    """원가(USD)를 포인트로 차감. 반환: (차감 포인트, 차감 후 잔액)."""
    points = math.ceil(cost_usd * POINTS_PER_USD) if cost_usd > 0 else 0
    wallet = get_wallet(user)
    if points:
        Wallet.objects.filter(pk=wallet.pk).update(
            balance=models.F("balance") - points)
        wallet.refresh_from_db(fields=["balance"])
    return points, wallet.balance
