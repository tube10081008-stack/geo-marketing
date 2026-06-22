"""Django Admin — 임상가 대신 '마케팅 운영자'용 수기 입력/검수 도구(intake §3, 등급 C)."""
from django.contrib import admin

from .models import (
    AcquisitionBaseline,
    ConversionBaseline,
    CustomerTransaction,
    Merchant,
    RetentionBaseline,
)


class AcquisitionInline(admin.StackedInline):
    model = AcquisitionBaseline
    extra = 0


class ConversionInline(admin.StackedInline):
    model = ConversionBaseline
    extra = 0


class RetentionInline(admin.StackedInline):
    model = RetentionBaseline
    extra = 0


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["name", "industry", "location", "overall_grade", "created_at"]
    list_filter = ["industry", "consent_data_processing"]
    search_fields = ["name", "location", "sub_category"]
    inlines = [AcquisitionInline, ConversionInline, RetentionInline]

    @admin.display(description="종합 데이터등급")
    def overall_grade(self, obj):
        return obj.overall_grade() or "—"


@admin.register(CustomerTransaction)
class CustomerTransactionAdmin(admin.ModelAdmin):
    list_display = ["merchant", "customer_key", "occurred_on", "amount"]
    list_filter = ["merchant"]
    search_fields = ["customer_key"]
    date_hierarchy = "occurred_on"
