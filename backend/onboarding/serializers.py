"""
온보딩 시리얼라이저 = API '폼'.

- 개별 베이스라인 시리얼라이저(3 페르소나)
- MerchantIntakeSerializer: 첫 만남에서 업체 + 3 베이스라인을 **한 번에** 등록(중첩 쓰기)
- BaselineCardSerializer: 베이스라인 카드(읽기 전용 산출물)
"""
from rest_framework import serializers

from .models import (
    AcquisitionBaseline,
    ConversionBaseline,
    CustomerTransaction,
    Merchant,
    RetentionBaseline,
)


class AcquisitionBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcquisitionBaseline
        exclude = ["id", "merchant"]


class ConversionBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversionBaseline
        exclude = ["id", "merchant"]


class RetentionBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetentionBaseline
        exclude = ["id", "merchant"]


class CustomerTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerTransaction
        fields = ["id", "merchant", "customer_key", "occurred_on", "amount"]


class MerchantIntakeSerializer(serializers.ModelSerializer):
    """첫 만남 통합 폼: 업체 공통 + 3 페르소나 베이스라인(선택)."""

    acquisition = AcquisitionBaselineSerializer(required=False)
    conversion = ConversionBaselineSerializer(required=False)
    retention = RetentionBaselineSerializer(required=False)
    overall_data_grade = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = [
            "id", "name", "industry", "sub_category", "location",
            "monthly_revenue", "variable_cost_ratio", "capacity", "business_days_per_week",
            "channels", "growth_goal_pct", "consent_data_processing",
            "acquisition", "conversion", "retention",
            "overall_data_grade", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_overall_data_grade(self, obj) -> str | None:
        return obj.overall_grade()

    def validate(self, attrs):
        """거래로그 수집(유지)엔 동의가 전제 — 동의 없이 멤버십 운영 주장 시 경고 차단은 뷰에서."""
        return attrs

    def create(self, validated_data):
        acq = validated_data.pop("acquisition", None)
        cvr = validated_data.pop("conversion", None)
        ret = validated_data.pop("retention", None)
        merchant = Merchant.objects.create(**validated_data)
        if acq is not None:
            AcquisitionBaseline.objects.create(merchant=merchant, **acq)
        if cvr is not None:
            ConversionBaseline.objects.create(merchant=merchant, **cvr)
        if ret is not None:
            RetentionBaseline.objects.create(merchant=merchant, **ret)
        return merchant

    def update(self, instance, validated_data):
        nested = {
            "acquisition": (AcquisitionBaseline, validated_data.pop("acquisition", None)),
            "conversion": (ConversionBaseline, validated_data.pop("conversion", None)),
            "retention": (RetentionBaseline, validated_data.pop("retention", None)),
        }
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        for attr, (model_cls, data) in nested.items():
            if data is None:
                continue
            model_cls.objects.update_or_create(merchant=instance, defaults=data)
        return instance


class BaselineCardSerializer(serializers.Serializer):
    """베이스라인 카드(읽기 전용) — Merchant.baseline_card() 출력을 그대로 전달."""

    def to_representation(self, instance):
        return instance.baseline_card()
