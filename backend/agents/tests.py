"""업종 벤치마크 차분(루프 ⑤) 테스트 — 익명 집계·표본 게이트·중앙값·정직 규칙."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from onboarding.models import KpiSnapshot, Merchant

from .benchmark import MIN_SAMPLE, MIN_SPAN_DAYS, benchmark_note, benchmark_rows

User = get_user_model()


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
