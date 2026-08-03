"""문헌 스크리너 — 근거의 '허용 주장 강도'를 코드로 게이팅한다.

SCREENING.md의 루브릭을 결정론적으로 재계산한다. 스펙 지시대로 최종 점수는
모델 산술을 신뢰하지 않고 백엔드가 다시 계산한다(LLM이 낸 F는 참고값일 뿐).

핵심 설계:
- U = (R·T·A·M·P)^(1/5) — 기하평균이라 한 축이 0에 가까우면 전체가 무너진다(곱셈적 게이트).
- E = sqrt(I·V) — 근거 신뢰도는 '연구 품질'과 '우리가 실제로 확인한 정도'의 결합.
- F = U·E
- S(안전·반론 가치)는 F에 넣지 않는다. F가 낮아도 반증으로 보존하는 브레이크 역할.

'정보 없음'과 '안전함'은 다르다 — 미확인 값은 0이 아니라 검증상태 상한으로 눌린다.
"""
from dataclasses import dataclass, field
from math import sqrt

# 원문 확인 수준별 V(검증 신뢰도) 상한 — 초록·2차요약으로 인과·효과크기를 확정할 수 없다
VERIFICATION_CAPS = {
    "full_text_verified": 1.0,
    "abstract_only": 0.6,
    "metadata_only": 0.3,
    "secondary_summary_only": 0.3,
    "unverified": 0.1,
}

ROLE_PRIORITY = [
    "rejected",
    "pending_verification",
    "counter_evidence_brake",
    "experiment_candidate",
    "hypothesis_generator",
    "context_qualifier",
]

# 역할 → 배치 등급. 외부 문헌만으로 자동 실행은 어떤 경우에도 금지.
_DEPLOYMENT_BY_ROLE = {
    "rejected": "blocked",
    "pending_verification": "blocked",
    "experiment_candidate": "recommendation_eligible",
    "hypothesis_generator": "local_test_only",
    "counter_evidence_brake": "evidence_context_only",
    "context_qualifier": "evidence_context_only",
}

_SCORE_FIELDS = (
    "relevance", "internal_validity", "transferability", "actionability",
    "measurability", "profit_proximity", "safety_counterevidence_value",
    "verification_confidence",
)


def _r4(x):
    return round(x + 0.0, 4)


@dataclass
class ClaimScores:
    """개별 focal claim의 구성 점수(0.0~1.0). 논문 단위가 아니라 주장 단위."""

    relevance: float = 0.0                     # R
    internal_validity: float = 0.0             # I
    transferability: float = 0.0               # T
    actionability: float = 0.0                 # A
    measurability: float = 0.0                 # M
    profit_proximity: float = 0.0              # P
    safety_counterevidence_value: float = 0.0  # S — F에 미포함(브레이크 전용)
    verification_confidence: float = 0.0       # V

    verification_status: str = "unverified"
    critical_risk: bool = False
    retraction_or_concern: bool = False
    claim_data_mismatch: bool = False
    notes: list = field(default_factory=list)

    def __post_init__(self):
        for f in _SCORE_FIELDS:
            v = getattr(self, f)
            if not 0.0 <= float(v) <= 1.0:
                raise ValueError(f"{f}는 0.0~1.0 범위여야 합니다(받은 값: {v})")
        if self.verification_status not in VERIFICATION_CAPS:
            raise ValueError(f"알 수 없는 검증 상태: {self.verification_status}")

    @property
    def effective_v(self) -> float:
        """확인 수준이 허용하는 상한으로 V를 누른다 — 자기신고 과신 방지."""
        return min(float(self.verification_confidence),
                   VERIFICATION_CAPS[self.verification_status])


def product_utility(s: ClaimScores) -> float:
    """U = (R·T·A·M·P)^(1/5) — 한 축이라도 결정적으로 낮으면 전체가 낮아진다."""
    prod = (s.relevance * s.transferability * s.actionability
            * s.measurability * s.profit_proximity)
    return _r4(prod ** (1 / 5) if prod > 0 else 0.0)


def evidence_strength(s: ClaimScores) -> float:
    """E = sqrt(I·V) — 연구 품질과 확인 수준의 결합(둘 중 하나만 높아도 못 올라간다)."""
    return _r4(sqrt(s.internal_validity * s.effective_v))


def final_weight(s: ClaimScores) -> float:
    """F = U·E. S는 포함하지 않는다."""
    return _r4(product_utility(s) * evidence_strength(s))


def classify_roles(s: ClaimScores) -> list:
    """역할 분류(중복 가능). counter_evidence_brake는 비차단 역할과 공존한다."""
    v, e, f = s.effective_v, evidence_strength(s), final_weight(s)
    roles = []

    if s.retraction_or_concern or s.claim_data_mismatch or (
            s.relevance < 0.2 and s.safety_counterevidence_value < 0.4):
        return ["rejected"]

    # 반증 브레이크는 검증 미달과 무관하게 먼저 판정 — F가 낮아도 보존 가치가 있다
    if s.safety_counterevidence_value >= 0.7 and e >= 0.5 and s.relevance >= 0.4:
        roles.append("counter_evidence_brake")

    if v < 0.4:
        roles.append("pending_verification")
        return roles

    if (f >= 0.55 and s.internal_validity >= 0.6 and v >= 0.7
            and s.profit_proximity >= 0.5 and not s.critical_risk):
        roles.append("experiment_candidate")
    elif (f >= 0.35 and s.relevance >= 0.5 and s.actionability >= 0.5
            and v >= 0.5):
        roles.append("hypothesis_generator")

    if not roles and s.relevance >= 0.4:
        roles.append("context_qualifier")
    return roles


def primary_role(roles: list) -> str | None:
    for r in ROLE_PRIORITY:
        if r in roles:
            return r
    return None


def deployment_status(s: ClaimScores, roles: list) -> str:
    """배치 등급 — 중대 위험이나 검증 미달이면 어떤 점수든 blocked."""
    if s.critical_risk:
        return "blocked"
    return _DEPLOYMENT_BY_ROLE.get(primary_role(roles), "blocked")


def screen(s: ClaimScores) -> dict:
    """구성 점수 → 재계산된 판정. 뷰·코퍼스 승격 심사의 단일 진입점."""
    roles = classify_roles(s)
    return {
        "calculated_scores": {
            "product_utility": product_utility(s),
            "evidence_strength": evidence_strength(s),
            "final_weight": final_weight(s),
        },
        "effective_verification_confidence": _r4(s.effective_v),
        "roles": roles,
        "primary_role": primary_role(roles),
        "deployment_status": deployment_status(s, roles),
    }
