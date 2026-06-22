"""
서버사이드 ModelForm (Django Admin / 비-API 화면용).

API는 serializers.py를 쓴다. 이 폼은 어드민·관리도구에서 사장님 정보를
사람이 직접 입력하는 용도(intake §3 첫 만남 수기 케이스, 등급 C).
"""
from django import forms

from .models import Merchant


class MerchantIntakeForm(forms.ModelForm):
    class Meta:
        model = Merchant
        fields = [
            "name", "industry", "sub_category", "location",
            "monthly_revenue", "capacity", "business_days_per_week",
            "channels", "growth_goal_pct", "consent_data_processing",
        ]

    def clean(self):
        cleaned = super().clean()
        # 거래로그(유지) 수집을 전제로 한 동의 안내 — 폼 단계 가벼운 검증.
        return cleaned
