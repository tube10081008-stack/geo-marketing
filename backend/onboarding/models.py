"""
온보딩 기초자료 수집(베이스라인) 데이터 모델.

설계 출처: ../../onboarding-intake.md (최소 데이터셋 §2), ../../orchestrator.md (KPI 트리 §4).
원칙: **측정 없이 성과 약속 금지.** 모든 베이스라인엔 데이터 신뢰등급(A/B/C/D)을 부여하고,
등급이 낮으면 추정임을 명시한다(intake §1).

구조:
- Merchant              : 소상공인 공통 프로필 (intake §2.0)
- AcquisitionBaseline   : 🎣 획득 KPI 베이스라인 (intake §2.1) — 신규유입·CAC·노출
- ConversionBaseline    : 💳 전환 KPI 베이스라인 (intake §2.2) — 전환율·객단가·평점
- RetentionBaseline     : 🔁 유지 KPI 베이스라인 (intake §2.3) — 재방문·LTV·이탈
- CustomerTransaction   : RFM/CLV 원천 거래로그 [M8] — ※가명·민감정보 (intake §4)
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class DataGrade(models.TextChoices):
    """데이터 신뢰등급 (intake §1). 낮을수록 추정 비중↑ → 성과 약속 강도↓."""

    A_INTEGRATION = "A", "A 연동(POS·플레이스 API)"
    B_EXPORT = "B", "B 내보내기(엑셀·정산서)"
    C_MANUAL = "C", "C 수기(사장님 입력)"
    D_ESTIMATE = "D", "D 추정(업종평균·모델)"


# 등급 → 신뢰 가중치(베이스라인 종합 신뢰도 계산용)
GRADE_WEIGHT = {"A": 1.0, "B": 0.8, "C": 0.55, "D": 0.3}


class Merchant(models.Model):
    """소상공인 업체 — 온보딩 공통 프로필 (intake §2.0 / 지오 공통)."""

    class Industry(models.TextChoices):
        FOOD = "food", "외식/음식점"
        BEAUTY = "beauty", "미용/뷰티"
        RETAIL = "retail", "소매/판매"
        CAFE = "cafe", "카페/디저트"
        SERVICE = "service", "생활서비스"
        ETC = "etc", "기타"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="소유 계정",
        on_delete=models.CASCADE, related_name="merchants", null=True, blank=True,
        help_text="계정별 워크스페이스. 각 사용자는 자기 업체만 조회/수정.",
    )
    name = models.CharField("상호", max_length=200)
    industry = models.CharField("업종", max_length=20, choices=Industry.choices)
    sub_category = models.CharField(
        "세부 카테고리", max_length=100, blank=True, default="",
        help_text="예: 고깃집, 네일샵 — 코퍼스/벤치마크 라우팅",
    )
    location = models.CharField(
        "위치·상권", max_length=200,
        help_text="동/역세권/배후 — [M1] 물리적 가용성",
    )
    monthly_revenue = models.PositiveBigIntegerField(
        "월 매출(최근 3개월 평균, 원)", null=True, blank=True,
        help_text="KPI 트리 분모. 미확보 시 추정(D).",
    )
    capacity = models.PositiveIntegerField(
        "좌석/객수 캐파", null=True, blank=True,
        help_text="추정모델 상한",
    )
    business_days_per_week = models.PositiveSmallIntegerField(
        "주 영업일", null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    channels = models.JSONField(
        "운영 채널", default=list, blank=True,
        help_text='예: ["naver_place","instagram","baemin"] — 연동 후보',
    )
    growth_goal_pct = models.DecimalField(
        "90일 목표 성장률(%)", max_digits=5, decimal_places=1, null=True, blank=True,
        help_text="예: 15.0 → 3개월 +15%",
    )
    consent_data_processing = models.BooleanField(
        "고객데이터 위탁처리 동의", default=False,
        help_text="intake §4 개인정보 게이트. 미동의 시 거래로그 수집 불가.",
    )
    chat_summary = models.TextField(
        "대화 장기기억 요약", blank=True, default="",
        help_text="최근 창 밖의 과거 대화를 저가 모델로 압축한 롤링 요약 — "
                  "전체 이력을 매번 보내지 않고도 다음 대화가 과거를 참조.",
    )
    chat_summary_upto = models.PositiveIntegerField(
        "요약 반영된 마지막 메시지 ID", default=0,
        help_text="이 ID 이하의 메시지는 chat_summary에 병합 완료.",
    )
    created_at = models.DateTimeField("생성", auto_now_add=True)
    updated_at = models.DateTimeField("수정", auto_now=True)

    class Meta:
        verbose_name = "소상공인 업체"
        verbose_name_plural = "소상공인 업체"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_industry_display()})"

    def overall_grade(self) -> str | None:
        """3 페르소나 베이스라인 중 **가장 낮은 등급**(보수적 종합 신뢰도)."""
        grades = []
        for attr in ("acquisition", "conversion", "retention"):
            base = getattr(self, attr, None)
            if base is not None:
                grades.append(base.data_grade)
        if not grades:
            return None
        # A < B < C < D 순으로 '나쁨' → 최악(가장 뒤) 반환
        order = ["A", "B", "C", "D"]
        return max(grades, key=order.index)

    def baseline_card(self) -> dict:
        """첫 만남 산출물: 퍼널 KPI 스냅샷 + 데이터등급 (intake §3, orchestrator §4.2).

        귀인은 소상공인 환경에서 불완전하므로, 모든 투영(projection)은
        '추정'으로 명시한다(과대약속 금지, orchestrator §4.3).
        """
        acq = getattr(self, "acquisition", None)
        cvr = getattr(self, "conversion", None)
        ret = getattr(self, "retention", None)

        card: dict = {
            "merchant": self.name,
            "overall_data_grade": self.overall_grade(),
            "acquisition": None,
            "conversion": None,
            "retention": None,
            "rough_projection": None,
            "disclaimer": "투영치는 베이스라인 기반 추정이며 인과 보장이 아님(orchestrator §4.3).",
        }

        if acq:
            card["acquisition"] = {
                "monthly_traffic_proxy": acq.place_clicks,
                "review_count": acq.review_count,
                "avg_rating": float(acq.avg_rating) if acq.avg_rating is not None else None,
                "grade": acq.data_grade,
            }
        if cvr:
            card["conversion"] = {
                "aov_krw": cvr.aov,
                "monthly_customers": cvr.monthly_customers,
                "visit_to_purchase_rate_pct": (
                    float(cvr.visit_to_purchase_rate)
                    if cvr.visit_to_purchase_rate is not None else None
                ),
                "grade": cvr.data_grade,
            }
        if ret:
            card["retention"] = {
                "revisit_rate_pct": (
                    float(ret.revisit_rate) if ret.revisit_rate is not None else None
                ),
                "regular_ratio_pct": (
                    float(ret.regular_ratio) if ret.regular_ratio is not None else None
                ),
                "nps": ret.nps,
                "grade": ret.data_grade,
            }

        # 거친 투영: 월 획득매출 ≈ 유입 × 전환율 × 객단가  (orchestrator §4.2)
        if acq and cvr and acq.place_clicks and cvr.visit_to_purchase_rate and cvr.aov:
            proj = (
                acq.place_clicks
                * float(cvr.visit_to_purchase_rate) / 100.0
                * cvr.aov
            )
            card["rough_projection"] = {
                "projected_monthly_acq_revenue_krw": round(proj),
                "formula": "place_clicks × 전환율 × 객단가",
            }
        return card


class AcquisitionBaseline(models.Model):
    """🎣 획득 베이스라인 (intake §2.1). KPI: 신규유입·CAC·노출. 근거 [M1][M11][M15]."""

    merchant = models.OneToOneField(
        Merchant, on_delete=models.CASCADE, related_name="acquisition",
        verbose_name="업체",
    )
    place_impressions = models.PositiveIntegerField(
        "네이버 플레이스 노출수(월)", null=True, blank=True, help_text="[M1] 가용성",
    )
    place_clicks = models.PositiveIntegerField(
        "플레이스 클릭(월)", null=True, blank=True, help_text="유입 proxy",
    )
    place_directions = models.PositiveIntegerField(
        "길찾기(월)", null=True, blank=True,
    )
    place_calls = models.PositiveIntegerField(
        "전화 연결(월)", null=True, blank=True,
    )
    review_count = models.PositiveIntegerField(
        "리뷰 개수(채널 합)", null=True, blank=True, help_text="[M11][M15]",
    )
    avg_rating = models.DecimalField(
        "평균 평점", max_digits=2, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="0~5. [M11] 평점 1↑→매출 5~9%↑(독립업체)",
    )
    new_visitor_ratio = models.DecimalField(
        "신규방문 비중(%)", max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    monthly_ad_spend = models.PositiveIntegerField(
        "월 광고비(원)", null=True, blank=True, help_text="CAC·ROAS",
    )
    data_grade = models.CharField(
        "데이터 등급", max_length=1, choices=DataGrade.choices,
        default=DataGrade.C_MANUAL,
    )

    class Meta:
        verbose_name = "획득 베이스라인"
        verbose_name_plural = "획득 베이스라인"

    def __str__(self):
        return f"{self.merchant.name} · 획득({self.data_grade})"


class ConversionBaseline(models.Model):
    """💳 전환 베이스라인 (intake §2.2). KPI: 전환율·객단가·평점. 근거 [M4][M5][M12][M13]."""

    merchant = models.OneToOneField(
        Merchant, on_delete=models.CASCADE, related_name="conversion",
        verbose_name="업체",
    )
    aov = models.PositiveIntegerField(
        "객단가 AOV(원)", null=True, blank=True, help_text="매출/객수",
    )
    monthly_customers = models.PositiveIntegerField(
        "월 고객수(명)", null=True, blank=True,
        help_text="intake v1.1 — 사장님이 아는 숫자. 객단가 자동계산의 분모",
    )
    visit_to_purchase_rate = models.DecimalField(
        "방문→구매 전환율(%)", max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="[M12] 반별점↑→매진 49%↑",
    )
    current_offer = models.CharField(
        "현재 오퍼·가격구조", max_length=300, blank=True, default="",
        help_text="[M4] 손실프레임 / [M5] 가격끝자리",
    )
    negative_review_monthly = models.PositiveIntegerField(
        "월 부정리뷰 수", null=True, blank=True,
        help_text="[M13] 부정리뷰가 더 무겁다 → 신속대응",
    )
    data_grade = models.CharField(
        "데이터 등급", max_length=1, choices=DataGrade.choices,
        default=DataGrade.C_MANUAL,
    )

    class Meta:
        verbose_name = "전환 베이스라인"
        verbose_name_plural = "전환 베이스라인"

    def __str__(self):
        return f"{self.merchant.name} · 전환({self.data_grade})"


class RetentionBaseline(models.Model):
    """🔁 유지 베이스라인 (intake §2.3). KPI: 재방문·LTV·이탈. 근거 [M7][M8][M9][M10]."""

    merchant = models.OneToOneField(
        Merchant, on_delete=models.CASCADE, related_name="retention",
        verbose_name="업체",
    )
    revisit_rate = models.DecimalField(
        "재방문율(%)", max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="[M7] 이탈 5%↓→이익 25~85%↑",
    )
    regular_ratio = models.DecimalField(
        "단골 비중(%)", max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    repurchase_cycle_days = models.PositiveIntegerField(
        "재구매 주기(일)", null=True, blank=True, help_text="[M8] RFM",
    )
    has_membership = models.BooleanField("멤버십/적립 운영", default=False)
    nps = models.SmallIntegerField(
        "NPS", null=True, blank=True,
        validators=[MinValueValidator(-100), MaxValueValidator(100)],
        help_text="[M9] -100~100",
    )
    data_grade = models.CharField(
        "데이터 등급", max_length=1, choices=DataGrade.choices,
        default=DataGrade.C_MANUAL,
    )

    class Meta:
        verbose_name = "유지 베이스라인"
        verbose_name_plural = "유지 베이스라인"

    def __str__(self):
        return f"{self.merchant.name} · 유지({self.data_grade})"


class CustomerTransaction(models.Model):
    """RFM/CLV 원천 거래로그 [M8] (intake §2.3, §4).

    ※ 민감정보: 고객 식별은 **가명 키(customer_key)**만 저장(이름·연락처 원본 미수집).
    위탁처리 동의(Merchant.consent_data_processing) 전제. 목적: 마케팅 KPI 한정.
    """

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="transactions",
        verbose_name="업체",
    )
    customer_key = models.CharField(
        "고객 가명키", max_length=64,
        help_text="해시/가명 식별자. 원본 PII 저장 금지.",
    )
    occurred_on = models.DateField("거래일")
    amount = models.PositiveIntegerField("거래금액(원)")

    class Meta:
        verbose_name = "거래로그"
        verbose_name_plural = "거래로그"
        ordering = ["-occurred_on"]
        indexes = [
            models.Index(fields=["merchant", "customer_key"]),
            models.Index(fields=["merchant", "occurred_on"]),
        ]

    def __str__(self):
        return f"{self.merchant.name} · {self.customer_key} · {self.occurred_on}"


class ChatMessage(models.Model):
    """에이전트 대화 영속(세션 지속) — 업체(워크스페이스)별 대화 로그."""

    class Role(models.TextChoices):
        USER = "user", "사용자"
        AGENT = "agent", "에이전트"
        SYSTEM = "system", "시스템"

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="messages", verbose_name="업체",
    )
    role = models.CharField("역할", max_length=8, choices=Role.choices)
    persona = models.CharField("페르소나", max_length=8, blank=True, default="")  # acq|cvr|ret
    text = models.TextField("내용")
    created_at = models.DateTimeField("작성", auto_now_add=True)

    class Meta:
        verbose_name = "대화 메시지"
        verbose_name_plural = "대화 메시지"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["merchant", "created_at"])]

    def __str__(self):
        return f"{self.merchant.name} · {self.role}: {self.text[:24]}"


class DeepReport(models.Model):
    """P2.5 지오 딥리포트 — Marlin-lite (RESEARCH-treequest-marlin.md §2).

    월간 초맞춤 리포트 잡(비동기). 원가 목표: 라이트 ~$0.05 / 딥 ~$0.15.
    """

    class Tier(models.TextChoices):
        LITE = "lite", "라이트(플레인 파이프라인)"
        DEEP = "deep", "딥(핵심 섹션 AB-MCTS 탐색)"

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        RUNNING = "running", "생성 중"
        DONE = "done", "완료"
        FAILED = "failed", "실패"

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="reports", verbose_name="업체",
    )
    tier = models.CharField("티어", max_length=8, choices=Tier.choices, default=Tier.LITE)
    status = models.CharField("상태", max_length=8, choices=Status.choices, default=Status.PENDING)
    content_md = models.TextField("리포트(Markdown)", blank=True, default="")
    meta = models.JSONField("메타(호출·비용·탐색로그)", default=dict, blank=True)
    credits_charged = models.PositiveIntegerField("청구 크레딧", default=0)
    error = models.TextField("오류", blank=True, default="")
    created_at = models.DateTimeField("요청", auto_now_add=True)
    finished_at = models.DateTimeField("완료", null=True, blank=True)

    class Meta:
        verbose_name = "딥리포트"
        verbose_name_plural = "딥리포트"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.merchant.name} · {self.tier} · {self.status}"


# 스냅샷·액션 공용 지표 키 (agents.views._UPDATE_MAP과 정합)
METRIC_CHOICES = [
    ("avg_rating", "평점"), ("review_count", "리뷰수"), ("place_clicks", "플레이스클릭"),
    ("aov", "객단가"), ("monthly_customers", "월 고객수"),
    ("visit_to_purchase_rate", "전환율"), ("revisit_rate", "재방문율"),
    ("regular_ratio", "단골비중"), ("nps", "NPS"), ("monthly_revenue", "월 매출"),
]


class KpiSnapshot(models.Model):
    """지표 시계열 — 덮어쓰기 대신 축적. 조언→실측 검증 루프의 기반(전/후 비교 가능).

    소스: intake(데이터 입력 폼) / chat(대화 중 추출) / checkin(주간 체크인, P다음).
    """

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="snapshots", verbose_name="업체")
    metric = models.CharField("지표", max_length=32, choices=METRIC_CHOICES)
    value = models.DecimalField("값", max_digits=14, decimal_places=2)
    source = models.CharField("출처", max_length=12, default="chat")  # intake|chat|checkin
    created_at = models.DateTimeField("측정", auto_now_add=True)

    class Meta:
        verbose_name = "KPI 스냅샷"
        verbose_name_plural = "KPI 스냅샷"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["merchant", "metric", "created_at"])]

    def __str__(self):
        return f"{self.merchant.name} · {self.metric}={self.value} ({self.source})"


def record_snapshot(merchant, metric, value, source="chat"):
    """스냅샷 축적 — 직전 값과 같으면 스킵(중복 방지). 숫자 아니면 무시."""
    from decimal import Decimal, InvalidOperation

    try:
        dv = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None
    last = merchant.snapshots.filter(metric=metric).last()
    if last and last.value == dv:
        return last
    return KpiSnapshot.objects.create(
        merchant=merchant, metric=metric, value=dv, source=source)


class Action(models.Model):
    """액션 티켓 — 에이전트 조언을 추적 가능한 객체로 (제안→실행→실측 마감).

    시작 시 baseline_value(당시 최신 스냅샷), 완료 시 result_value를 박아
    전/후 델타가 남는다. 이 레코드가 쌓이면 [M#] 근거의 국내 실측 격상 재료가 된다.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", "제안됨"
        ACTIVE = "active", "실행 중"
        DONE = "done", "완료(실측)"
        DROPPED = "dropped", "중단"

    class Protocol(models.TextChoices):
        SIMPLE = "simple", "전후 비교"
        ABAB = "abab", "격주 on-off 교차"

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="actions", verbose_name="업체")
    protocol = models.CharField(
        "검증 프로토콜", max_length=8, choices=Protocol.choices, default=Protocol.SIMPLE,
        help_text="abab=격주로 실행/중단을 교차해 국면 평균을 비교(계절성·추세에 강함)")
    persona = models.CharField("담당", max_length=8)  # acq|cvr|ret
    title = models.CharField("액션 요약", max_length=300)
    detail = models.TextField("상세", blank=True, default="")
    target_metric = models.CharField(
        "타깃 지표", max_length=32, choices=METRIC_CHOICES, blank=True, default="")
    citations = models.JSONField("근거 인용", default=list, blank=True)
    status = models.CharField("상태", max_length=10, choices=Status.choices,
                              default=Status.PROPOSED)
    baseline_value = models.DecimalField(
        "시작 시점 값", max_digits=14, decimal_places=2, null=True, blank=True)
    result_value = models.DecimalField(
        "완료 시점 값", max_digits=14, decimal_places=2, null=True, blank=True)
    started_at = models.DateTimeField("시작", null=True, blank=True)
    closed_at = models.DateTimeField("마감", null=True, blank=True)
    created_at = models.DateTimeField("제안", auto_now_add=True)

    class Meta:
        verbose_name = "액션 티켓"
        verbose_name_plural = "액션 티켓"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["merchant", "status"])]

    def delta(self):
        """전/후 변화량(%) — 둘 다 있어야 산출. '신호'이지 '증명'이 아니다."""
        if self.baseline_value in (None, 0) or self.result_value is None:
            return None
        return round(
            (float(self.result_value) - float(self.baseline_value))
            / float(self.baseline_value) * 100, 1)

    def abab_phase(self, at=None):
        """격주 국면: 시작 후 짝수 주=실행(on), 홀수 주=중단(off)."""
        from django.utils import timezone

        if self.protocol != self.Protocol.ABAB or not self.started_at:
            return None
        weeks = ((at or timezone.now()) - self.started_at).days // 7
        return "on" if weeks % 2 == 0 else "off"

    def abab_result(self):
        """실행 주 스냅샷 평균 vs 중단 주 평균 — 국면별 1개 이상일 때만(교차설계 귀인)."""
        if self.protocol != self.Protocol.ABAB or not (self.started_at and self.target_metric):
            return None
        snaps = self.merchant.snapshots.filter(
            metric=self.target_metric, created_at__gt=self.started_at)
        if self.closed_at:
            snaps = snaps.filter(created_at__lte=self.closed_at)
        on, off = [], []
        for s in snaps:
            (on if self.abab_phase(s.created_at) == "on" else off).append(float(s.value))
        if not on or not off:
            return None
        mo, mf = sum(on) / len(on), sum(off) / len(off)
        return {"on_mean": round(mo, 2), "off_mean": round(mf, 2),
                "n_on": len(on), "n_off": len(off),
                "diff_pct": round((mo - mf) / mf * 100, 1) if mf else None}

    def __str__(self):
        return f"{self.merchant.name} · [{self.persona}] {self.title[:30]} ({self.status})"
