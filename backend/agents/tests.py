"""업종 벤치마크 차분(루프 ⑤) 테스트 — 익명 집계·표본 게이트·중앙값·정직 규칙."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from onboarding.models import KpiSnapshot, Merchant

from .benchmark import MIN_SAMPLE, MIN_SPAN_DAYS, benchmark_note, benchmark_rows
from .chat import _sanitize_citations, route
from .llm import Meter, get_provider

User = get_user_model()


class RouteTest(TestCase):
    """라우팅 회귀·근본원인 방어 — 멤버십/RFM류가 유지(ret)로 가야 한다."""

    def _route(self, msg):
        return route(msg, None, get_provider(), Meter())

    def test_membership_spelling_variant_routes_ret(self):
        # 실제 버그: '멤버쉽'(비표준 철자)이 키워드 '멤버십'과 달라 유지 미라우팅됐던 케이스
        picks = self._route(
            "지금 포스 시스템을 자체 개발해서 고객 멤버쉽을 다룰 예정이야! 어떤 부분을 참고하면 좋을까?")
        self.assertIn("ret", picks)

    def test_standard_membership_routes_ret(self):
        self.assertIn("ret", self._route("멤버십 운영 어떻게 하나요"))

    def test_rfm_routes_ret(self):
        self.assertIn("ret", self._route("RFM 데이터 수집의 기초개념 자세히 설명해줘"))

    def test_clv_loyalty_routes_ret(self):
        self.assertIn("ret", self._route("고객 생애가치(CLV)와 로열티 프로그램 알려줘"))

    def test_acquisition_still_routes_acq(self):
        # 회귀 방지: 기존 획득 키워드 라우팅은 그대로
        self.assertEqual(self._route("신규 손님 노출 늘리려면?"), ["acq"])


class CitationSanitizeTest(TestCase):
    """검증된 인용 강제 — 담당 코퍼스 밖 번호는 본문·칩에서 제거(정직 원칙)."""

    def test_keeps_only_valid_persona_citations(self):
        # 획득(acq): M15·M1은 acq 코퍼스 → 유지 / M16·M19는 유지(ret) 것 → 제거
        reply = "리뷰가 중요합니다 [M15]. 추천 고객 가치가 높고 [M1] 관계를 강화합니다 [M16]. 로열티 [M19]."
        cleaned, used = _sanitize_citations(reply, "acq")
        self.assertEqual(used, ["M15", "M1"])
        self.assertIn("[M15]", cleaned)
        self.assertNotIn("[M16]", cleaned)
        self.assertNotIn("[M19]", cleaned)

    def test_hallucinated_number_dropped(self):
        # 코퍼스에 없는 번호(M99)는 담당과 무관하게 제거
        cleaned, used = _sanitize_citations("테스트 [M99] 문장입니다", "cvr")
        self.assertEqual(used, [])
        self.assertNotIn("M99", cleaned)

    def test_dedupe_preserves_order(self):
        cleaned, used = _sanitize_citations("[M12] 그리고 [M14], 다시 [M12]", "cvr")
        self.assertEqual(used, ["M12", "M14"])

    def test_cora_tax_citations(self):
        # 코라(cora)는 [T#]만 유효, 섞여 든 [M1]은 제거
        cleaned, used = _sanitize_citations("부가세 신고 [T1], 그리고 [M1]은 무관", "cora")
        self.assertEqual(used, ["T1"])
        self.assertNotIn("[M1]", cleaned)

    def test_no_citation_noop(self):
        cleaned, used = _sanitize_citations("근거 없는 일반 답변입니다.", "acq")
        self.assertEqual(used, [])
        self.assertEqual(cleaned, "근거 없는 일반 답변입니다.")


def _snap(merchant, metric, value, days_ago):
    """created_at은 auto_now_add라 생성 후 update로 과거 시점을 박는다."""
    s = KpiSnapshot.objects.create(merchant=merchant, metric=metric, value=value)
    KpiSnapshot.objects.filter(pk=s.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago))
    return s


class BenchmarkTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner1", password="pass1234!")
        self.me = Merchant.objects.create(
            owner=self.user, name="내가게", industry="food", location="서울")
        # 내 시계열: 30일 전 100 → 오늘 110 (+10%)
        _snap(self.me, "review_count", 100, 30)
        _snap(self.me, "review_count", 110, 0)

    def _add_peers(self, count, growth_pct=5, industry="food"):
        for i in range(count):
            u = User.objects.create_user(f"peer{industry}{i}", password="pass1234!")
            m = Merchant.objects.create(
                owner=u, name=f"동종{i}", industry=industry, location="어딘가")
            _snap(m, "review_count", 200, 30)
            _snap(m, "review_count", 200 * (1 + growth_pct / 100), 0)

    def test_hidden_below_min_sample(self):
        """표본 n < MIN_SAMPLE이면 업종 값 숨김 + n은 정직 공개."""
        self._add_peers(MIN_SAMPLE - 1)
        (row,) = benchmark_rows(self.me)
        self.assertTrue(row["hidden"])
        self.assertEqual(row["n"], MIN_SAMPLE - 1)
        self.assertIsNone(row["industry_pct"])
        self.assertIsNone(row["diff_pct"])
        self.assertEqual(row["mine_pct"], 10.0)  # 내 증가율은 항상 보인다
        self.assertEqual(benchmark_note(self.me), "")  # 프롬프트에도 미주입

    def test_visible_at_min_sample_and_diff(self):
        """n = MIN_SAMPLE부터 공개 — 중앙값과 차분(diff-in-diff) 산출."""
        self._add_peers(MIN_SAMPLE, growth_pct=4)
        (row,) = benchmark_rows(self.me)
        self.assertFalse(row["hidden"])
        self.assertEqual(row["n"], MIN_SAMPLE)
        self.assertEqual(row["industry_pct"], 4.0)
        self.assertEqual(row["diff_pct"], 6.0)  # 10% - 4% = +6%p
        self.assertIn("업종 중앙값", benchmark_note(self.me))

    def test_self_excluded_from_industry(self):
        """자기 자신은 업종 집계에서 제외 — n에 포함되지 않는다."""
        self._add_peers(3)
        (row,) = benchmark_rows(self.me)
        self.assertEqual(row["n"], 3)

    def test_other_industry_not_counted(self):
        """다른 업종은 집계에서 제외(동종업종만 비교)."""
        self._add_peers(MIN_SAMPLE, industry="cafe")
        (row,) = benchmark_rows(self.me)
        self.assertEqual(row["n"], 0)
        self.assertTrue(row["hidden"])

    def test_median_resists_outlier(self):
        """극단값 1건이 있어도 중앙값은 왜곡되지 않는다."""
        self._add_peers(MIN_SAMPLE - 1, growth_pct=5)
        u = User.objects.create_user("outlier", password="pass1234!")
        m = Merchant.objects.create(owner=u, name="극단", industry="food", location="")
        _snap(m, "review_count", 10, 30)
        _snap(m, "review_count", 100, 0)  # +900%
        (row,) = benchmark_rows(self.me)
        self.assertEqual(row["n"], MIN_SAMPLE)
        self.assertEqual(row["industry_pct"], 5.0)

    def test_short_span_is_not_a_trend(self):
        """측정 간격 < MIN_SPAN_DAYS면 추세로 치지 않는다(내 것도, 동종 것도)."""
        u = User.objects.create_user("shorty", password="pass1234!")
        m = Merchant.objects.create(owner=u, name="짧은가게", industry="food", location="")
        _snap(m, "review_count", 100, MIN_SPAN_DAYS - 1)
        _snap(m, "review_count", 150, 0)
        (row,) = benchmark_rows(self.me)
        self.assertEqual(row["n"], 0)  # 간격 부족 → 표본 미포함
        self.assertEqual(benchmark_rows(m), [])  # 내 추세 없음 → 행 자체가 없다

    def test_zero_baseline_skipped(self):
        """첫 값이 0이면 증가율 미정의 — 조용히 제외(0나눗셈·무한대 방지)."""
        u = User.objects.create_user("zero", password="pass1234!")
        m = Merchant.objects.create(owner=u, name="제로", industry="food", location="")
        _snap(m, "review_count", 0, 30)
        _snap(m, "review_count", 50, 0)
        (row,) = benchmark_rows(self.me)
        self.assertEqual(row["n"], 0)

    def test_api_endpoint(self):
        """GET /agents/benchmark/ — 소유자 스코프 + 응답 스키마."""
        self._add_peers(MIN_SAMPLE)
        c = APIClient()
        token, _ = Token.objects.get_or_create(user=self.user)
        c.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        r = c.get(f"/api/v1/agents/benchmark/?merchant_id={self.me.pk}")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["industry"], "food")
        self.assertEqual(d["min_sample"], MIN_SAMPLE)
        self.assertEqual(len(d["rows"]), 1)
        # 남의 업체는 404
        other = User.objects.create_user("intruder", password="pass1234!")
        t2, _ = Token.objects.get_or_create(user=other)
        c.credentials(HTTP_AUTHORIZATION=f"Token {t2.key}")
        r = c.get(f"/api/v1/agents/benchmark/?merchant_id={self.me.pk}")
        self.assertEqual(r.status_code, 404)
