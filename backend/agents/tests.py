"""업종 벤치마크 차분(루프 ⑤) 테스트 — 익명 집계·표본 게이트·중앙값·정직 규칙."""
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from decimal import Decimal

from onboarding.models import (
    METRIC_CHOICES, ConversionBaseline, KpiSnapshot, Merchant,
    record_margin_snapshots,
)

from .benchmark import MIN_SAMPLE, MIN_SPAN_DAYS, benchmark_note, benchmark_rows
from .corpus import retrieve
from .notes import MAX_NOTES, _load_notes, notes_block, relevant_mids
from .chat import (
    DUPLICATE_RATIO, INDIRECT_EVIDENCE_INDUSTRIES, _is_duplicate,
    _max_tokens_for, _sanitize_citations, _system, extract_updates, route,
    run_chat,
)
from .views import AdviseView
from .screening import (
    ClaimScores, classify_roles, deployment_status, evidence_strength,
    final_weight, primary_role, product_utility,
)
from .llm import CHAT_MAX_TOKENS, Meter, Usage, get_provider

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


class ScreeningTest(TestCase):
    """문헌 스크리너 — 곱셈적 게이트·검증 상한·반증 보존을 코드가 집행하는지."""

    def _s(self, **kw):
        base = dict(relevance=0.8, internal_validity=0.8, transferability=0.8,
                    actionability=0.8, measurability=0.8, profit_proximity=0.8,
                    verification_confidence=1.0,
                    verification_status="full_text_verified")
        base.update(kw)
        return ClaimScores(**base)

    def test_multiplicative_gate_one_zero_axis_collapses(self):
        """한 축이 0이면 U가 무너진다 — 나머지가 아무리 높아도."""
        s = self._s(profit_proximity=0.0)
        self.assertEqual(product_utility(s), 0.0)
        self.assertEqual(final_weight(s), 0.0)

    def test_verification_cap_dominates_selfreported_v(self):
        """초록만 봤으면 V=1.0을 주장해도 0.6으로 눌린다."""
        s = self._s(verification_status="abstract_only", verification_confidence=1.0)
        self.assertEqual(s.effective_v, 0.6)
        # metadata_only는 0.3 → pending_verification 구간
        s2 = self._s(verification_status="metadata_only", verification_confidence=0.9)
        self.assertEqual(s2.effective_v, 0.3)
        self.assertIn("pending_verification", classify_roles(s2))

    def test_experiment_candidate_requires_full_verification(self):
        """실험 후보는 V>=0.7 — 초록만으로는 절대 도달 불가."""
        strong = self._s()
        self.assertIn("experiment_candidate", classify_roles(strong))
        abstract = self._s(verification_status="abstract_only")
        self.assertNotIn("experiment_candidate", classify_roles(abstract))

    def test_counter_evidence_preserved_despite_low_f(self):
        """반증은 F가 낮아도 보존된다(S는 F에 미포함)."""
        s = self._s(actionability=0.1, profit_proximity=0.1, measurability=0.2,
                    safety_counterevidence_value=0.9)
        self.assertLess(final_weight(s), 0.35)
        roles = classify_roles(s)
        self.assertIn("counter_evidence_brake", roles)
        self.assertEqual(primary_role(roles), "counter_evidence_brake")

    def test_critical_risk_blocks_regardless_of_score(self):
        """중대 위험이면 만점이어도 blocked."""
        s = self._s(critical_risk=True)
        self.assertEqual(deployment_status(s, classify_roles(s)), "blocked")

    def test_retraction_rejected(self):
        s = self._s(retraction_or_concern=True)
        self.assertEqual(classify_roles(s), ["rejected"])
        self.assertEqual(deployment_status(s, classify_roles(s)), "blocked")

    def test_hypothesis_generator_maps_to_local_test_only(self):
        """직접 권고가 아니라 로컬 검증 가설 — 배치는 local_test_only."""
        s = self._s(internal_validity=0.5, transferability=0.5, profit_proximity=0.5,
                    verification_status="abstract_only", verification_confidence=0.6)
        roles = classify_roles(s)
        self.assertEqual(primary_role(roles), "hypothesis_generator")
        self.assertEqual(deployment_status(s, roles), "local_test_only")

    def test_score_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            ClaimScores(relevance=1.5)

    def test_formula_matches_spec(self):
        """U=(R·T·A·M·P)^(1/5), E=sqrt(I·V), F=U·E 수식 일치."""
        s = self._s(relevance=0.5, transferability=0.5, actionability=0.5,
                    measurability=0.5, profit_proximity=0.5, internal_validity=0.64,
                    verification_confidence=1.0)
        self.assertEqual(product_utility(s), 0.5)
        self.assertEqual(evidence_strength(s), 0.8)
        self.assertEqual(final_weight(s), 0.4)


class ContributionMarginTest(TestCase):
    """증분 공헌이익 — 목적함수 교정. 매출만 보면 할인이 이익을 깎아도 '성공'이 된다."""

    def setUp(self):
        self.user = User.objects.create_user("cmowner", password="pass1234!")
        self.m = Merchant.objects.create(
            owner=self.user, name="마진카페", industry="cafe", location="서울",
            monthly_revenue=10_000_000)
        ConversionBaseline.objects.create(merchant=self.m, aov=10_000)

    def test_no_cost_ratio_means_no_fabricated_margin(self):
        """변동비율을 모르면 이익을 지어내지 않는다 — None이어야 한다."""
        self.assertIsNone(self.m.contribution_margin_rate())
        self.assertIsNone(self.m.monthly_contribution_margin())
        self.assertIsNone(self.m.unit_contribution_margin())
        self.assertEqual(record_margin_snapshots(self.m), [])
        card = self.m.baseline_card()
        self.assertIsNone(card["profit"]["contribution_margin_rate_pct"])
        self.assertIn("계산할 수 없다", card["profit"]["note"])

    def test_derived_margin_math(self):
        """변동비율 35% → 공헌이익률 65%, 월 650만, 주문당 6,500원."""
        self.m.variable_cost_ratio = Decimal("35.0")
        self.m.save()
        self.assertEqual(self.m.contribution_margin_rate(), Decimal("65.0"))
        self.assertEqual(self.m.monthly_contribution_margin(), 6_500_000)
        self.assertEqual(self.m.unit_contribution_margin(), 6_500)

    def test_revenue_up_but_margin_down_is_caught(self):
        """핵심 시나리오: 할인으로 매출은 +10%인데 공헌이익은 감소.

        매출만 재던 기존 구조에서는 '성공'으로 보이던 액션이다.
        """
        self.m.variable_cost_ratio = Decimal("35.0")
        self.m.save()
        record_margin_snapshots(self.m)              # 기준선: CM 650만
        before = self.m.monthly_contribution_margin()

        # 할인 시행 → 매출 +10%, 그러나 변동비율 35% → 50%로 악화
        self.m.monthly_revenue = 11_000_000
        self.m.variable_cost_ratio = Decimal("50.0")
        self.m.save()
        record_margin_snapshots(self.m)
        after = self.m.monthly_contribution_margin()

        self.assertGreater(self.m.monthly_revenue, 10_000_000)  # 매출은 늘었다
        self.assertLess(after, before)                          # 이익은 줄었다
        self.assertEqual(after, 5_500_000)
        # 시계열에도 하락이 남아 액션 델타가 이익 기준으로 판정된다
        snaps = list(self.m.snapshots.filter(metric="contribution_margin")
                     .values_list("value", flat=True))
        self.assertEqual([int(v) for v in snaps], [6_500_000, 5_500_000])

    def test_action_target_promotes_to_margin(self):
        """변동비율을 알면 전환 액션 타깃이 객단가 → 주문당 공헌이익으로 승격."""
        v = AdviseView()
        self.assertEqual(v._target_metric("cvr", self.m), "aov")
        self.m.variable_cost_ratio = Decimal("35.0")
        self.m.save()
        self.assertEqual(v._target_metric("cvr", self.m), "unit_contribution_margin")
        # 획득·유지 타깃은 그대로
        self.assertEqual(v._target_metric("acq", self.m), "review_count")

    def test_cost_ratio_extracted_from_chat(self):
        """대화에서 원가율 발화를 잡아낸다."""
        self.assertEqual(extract_updates("우리 원가율은 38% 정도예요"),
                         {"variable_cost_ratio": "38"})
        self.assertEqual(extract_updates("변동비 비율 42.5% 입니다"),
                         {"variable_cost_ratio": "42.5"})

    def test_checkin_records_cost_ratio_and_derives_margin(self):
        """주간 체크인으로 원가율이 들어오면 공헌이익 시계열이 살아난다."""
        c = APIClient()
        token, _ = Token.objects.get_or_create(user=self.user)
        c.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        r = c.post("/api/v1/agents/checkin/", {
            "merchant_id": self.m.pk, "monthly_revenue": 12_000_000,
            "variable_cost_ratio": "40.0"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.m.refresh_from_db()
        self.assertEqual(self.m.variable_cost_ratio, Decimal("40.0"))
        self.assertEqual(self.m.monthly_contribution_margin(), 7_200_000)
        self.assertTrue(self.m.snapshots.filter(metric="contribution_margin").exists())

    def test_margin_metrics_benchmarkable(self):
        """공헌이익도 업종 벤치마크 대상 지표에 포함된다(차분 비교 가능)."""
        self.assertIn("contribution_margin", dict(METRIC_CHOICES))
        self.assertIn("unit_contribution_margin", dict(METRIC_CHOICES))


class PersonaDisciplineTest(TestCase):
    """P2 — 정체성 붕괴·중복 발화·날짜 날조 방지. A — 발화 강도 상한."""

    def setUp(self):
        self.user = User.objects.create_user("disc", password="pass1234!")
        self.m = Merchant.objects.create(
            owner=self.user, name="규율카페", industry="cafe", location="서울")

    def _sys(self, persona, **kw):
        return _system(persona, self.m, self.m.baseline_card(), **kw)

    def test_identity_lock_in_every_persona(self):
        """모든 페르소나에 '다른 담당 자칭 금지'가 들어간다(획득이 유지를 자칭하던 버그)."""
        for p in ("acq", "cvr", "ret", "fugu", "cora"):
            self.assertIn("정체성 고정", self._sys(p))
            self.assertIn("사칭", self._sys(p))

    def test_claim_strength_gate_on_marketing_personas(self):
        """A — 마케팅 담당은 '검증해볼 가설'까지만 허용. 인과 표현·수치 약속 금지."""
        s = self._sys("acq")
        self.assertIn("검증해볼 가설", s)
        self.assertIn("인과 표현", s)
        self.assertIn("약속하지 마라", s)
        # 코라(세무)는 법령 근거라 이 규율 대상이 아니다
        self.assertNotIn("근거 강도", self._sys("cora"))

    def test_real_date_injected_not_fabricated(self):
        """오늘 날짜를 실제 값으로 주입 — '오늘(5월 17일)' 날조 방지."""
        today = timezone.localdate().isoformat()
        for p in ("acq", "cvr", "ret", "cora"):
            self.assertIn(today, self._sys(p))
        self.assertIn("날짜·요일·수치를 지어내지 마라", self._sys("acq"))

    def test_profit_rule_present(self):
        """목적함수(공헌이익) 규율 + 원가율 미상 고지."""
        s = self._sys("cvr")
        self.assertIn("증분 공헌이익", s)
        self.assertIn("공헌이익 미상", s)   # 변동비율 미입력 상태

    def test_duplicate_reply_detection(self):
        """동료 답변과 과도하게 겹치면 중복으로 판정."""
        a = "RFM으로 단골을 세분화하면 재방문 흐름을 볼 수 있습니다. 적립 설계가 중요합니다."
        self.assertTrue(_is_duplicate(a, a))                      # 완전 동일
        self.assertTrue(_is_duplicate(a, a[:-10] + " 감사합니다."))  # 거의 동일
        self.assertFalse(_is_duplicate(a, "객단가는 세트 구성으로 올릴 수 있습니다."))
        self.assertFalse(_is_duplicate(a, ""))                    # 첫 발화는 비교대상 없음

    def test_duplicate_second_turn_is_replaced(self):
        """2인 협업에서 두 번째가 첫 답변을 복붙하면 코드가 잘라낸다."""
        canned = "RFM으로 단골을 세분화하면 CLV를 추정할 수 있습니다 [M8]. 적립 설계가 핵심입니다."

        class Dup:
            name = "fake"; generator_model = router_model = "fake"
            def chat(self, system, messages, max_tokens):
                return canned, Usage(model="fake", input_tokens=5, output_tokens=5)
            def complete(self, model, system, prompt, max_tokens=12):
                return "ret,cvr", Usage(model="fake", input_tokens=1, output_tokens=1)

        r = run_chat(self.m, [], "단골 관리와 객단가를 같이 올리고 싶은데 방법이 있을까요?",
                     provider=Dup(), meter=Meter())
        self.assertEqual(len(r["turns"]), 2)
        self.assertFalse(r["turns"][0]["deduped"])
        self.assertTrue(r["turns"][1]["deduped"])
        self.assertEqual(r["turns"][1]["reply"], "덧붙일 내용은 없습니다.")
        self.assertEqual(r["turns"][1]["citations"], [])   # 중복이면 인용칩도 안 붙는다


class EvidenceNotesTest(TestCase):
    """2단 근거 — references.md 상세 노트를 질문 관련 항목만 펼친다."""

    def test_all_corpus_entries_parsed(self):
        """references.md의 [M1]~[M22]가 전부 파싱된다(드리프트 감지)."""
        notes = _load_notes()
        self.assertEqual(len(notes), 22)
        for n in (1, 8, 17, 22):
            self.assertTrue(notes.get(f"M{n}"), f"M{n} 노트 누락")

    def test_relevant_selection_is_topical(self):
        """RFM 질문엔 M8(RFM→CLV)이 선택된다 — 아무거나 펼치지 않는다."""
        ret_mids = [e.mid for e in retrieve("ret")]
        picked = relevant_mids("RFM으로 단골 세분화하고 CLV 추정하고 싶어요", ret_mids)
        self.assertIn("M8", picked)
        self.assertLessEqual(len(picked), MAX_NOTES)

    def test_no_match_yields_nothing(self):
        """관련 근거가 없으면 억지로 펼치지 않는다."""
        self.assertEqual(relevant_mids("오늘 날씨 어때요", [e.mid for e in retrieve("ret")]), [])
        self.assertEqual(notes_block("오늘 날씨 어때요", ["M8"]), "")

    def test_selection_confined_to_persona_corpus(self):
        """담당 코퍼스 밖 번호는 후보에 오르지 않는다(오배정 원천 차단)."""
        acq_mids = [e.mid for e in retrieve("acq")]
        self.assertNotIn("M8", relevant_mids("RFM 세분화 알려줘", acq_mids))

    def test_note_injected_into_prompt(self):
        """상세 노트가 실제 시스템 프롬프트에 들어간다 — 서지 디테일 포함."""
        u = User.objects.create_user("notes1", password="pass1234!")
        m = Merchant.objects.create(owner=u, name="노트카페", industry="cafe", location="서울")
        s = _system("ret", m, m.baseline_card(), message="RFM 세분화와 CLV 알려줘")
        self.assertIn("근거 상세", s)
        self.assertIn("BG/NBD", s)          # 한 줄 색인엔 없던 실제 디테일
        self.assertIn("지어내지 마라", s)
        # 관련 없는 질문이면 2단이 붙지 않는다(토큰 절약)
        self.assertNotIn("근거 상세", _system("ret", m, m.baseline_card(), message="안녕하세요"))

    def test_missing_file_degrades_quietly(self):
        """references.md를 못 찾아도 죽지 않고 빈 노트로 동작(배포 안전)."""
        _load_notes.cache_clear()
        with patch("agents.notes._SEARCH_PATHS", (Path("/nonexistent/references.md"),)):
            self.assertEqual(_load_notes(), {})
            self.assertEqual(notes_block("RFM 알려줘", ["M8"]), "")
        _load_notes.cache_clear()


class IndustryDisclosureTest(TestCase):
    """직접 실증 근거가 없는 업종은 '전이된 간접 근거'임을 고지한다(판매 정직성)."""

    def _sys(self, industry, persona="acq"):
        u = User.objects.create_user(f"ind{industry}", password="pass1234!")
        m = Merchant.objects.create(owner=u, name="가게", industry=industry, location="서울")
        return _system(persona, m, m.baseline_card())

    def test_beauty_gets_disclosure(self):
        """뷰티는 references.md가 실증 미확보를 자인한 업종 — 반드시 고지."""
        s = self._sys("beauty")
        self.assertIn("업종 근거 고지", s)
        self.assertIn("직접 실증 근거가 없다", s)
        self.assertIn("있는 것처럼 말하지 마라", s)

    def test_food_and_cafe_no_disclosure(self):
        """외식·카페·소매는 직접 근거가 있으므로 고지하지 않는다."""
        for ind in ("food", "cafe", "retail"):
            self.assertNotIn("업종 근거 고지", self._sys(ind))

    def test_disclosure_covers_all_indirect_industries(self):
        for ind in INDIRECT_EVIDENCE_INDUSTRIES:
            self.assertIn("업종 근거 고지", self._sys(ind))


class RoutingCoverageTest(TestCase):
    """20문 고객 감사에서 드러난 라우팅 공백 — 근거를 쥔 담당이 깨어나는가."""

    def _r(self, msg):
        return route(msg, None, get_provider(), Meter())

    def test_delivery_routes_to_acq(self):
        """배달 근거 M20·M21은 획득 소유 — 배달 질문에 획득이 깨어나야 한다."""
        for q in ("배달앱에 입점하는 게 나을까요?", "배민 수수료가 부담돼요",
                  "포장 주문을 늘리고 싶어요"):
            self.assertIn("acq", self._r(q), q)

    def test_negative_review_reaches_ret(self):
        """부정리뷰 대응 근거 M13은 유지 소유 — 유지가 깨어나야 인용 가능."""
        for q in ("부정 리뷰가 하나 달렸는데 어떻게 대응하죠?", "악평이 달렸어요"):
            self.assertIn("ret", self._r(q), q)

    def test_diagnosis_goes_to_fugu(self):
        """원인 미상의 종합 진단은 복어가 받는다(한 담당으로 좁히면 오진)."""
        for q in ("지난달보다 매출이 떨어졌어요", "요즘 손님이 줄었어요",
                  "장사가 안 돼요", "뭐부터 해야 할까요?"):
            self.assertEqual(self._r(q), ["fugu"], q)

    def test_out_of_scope_goes_to_fugu(self):
        """채용·노무·임대차는 팀 범위 밖 — 복어가 경계를 밝힌다."""
        for q in ("직원을 한 명 더 뽑아야 할지 고민이에요", "알바 채용 어떻게 하죠",
                  "월세 인상 통보를 받았어요"):
            self.assertEqual(self._r(q), ["fugu"], q)

    def test_tax_still_wins_over_out_of_scope(self):
        """4대보험·세금은 코라가 먼저 잡는다(범위밖 규칙에 뺏기지 않게)."""
        self.assertEqual(self._r("직원 4대 보험은 어떻게 처리하나요"), ["cora"])

    def test_existing_routes_unbroken(self):
        """회귀 방지 — 기존 라우팅은 그대로."""
        self.assertIn("acq", self._r("신규 손님 노출 늘리려면?"))
        self.assertIn("ret", self._r("고객 멤버쉽 만들 예정이야 뭘 참고할까"))
        self.assertEqual(self._r("부가세 신고 언제까지예요?"), ["cora"])
        self.assertEqual(self._r("복어야 정리해줘"), ["fugu"])

    def test_tier2_notes_for_conversion_basics(self):
        """객단가·세트 질문에도 상세근거가 붙는다(이전엔 미발동)."""
        cvr_mids = [e.mid for e in retrieve("cvr")]
        self.assertTrue(relevant_mids("객단가를 올리고 싶어요", cvr_mids))
        self.assertTrue(relevant_mids("세트 메뉴를 만들면 도움이 될까요?", cvr_mids))


class CollabAndLengthTest(TestCase):
    """실전 감사 후속 — 협업 중복(의미 단위)과 분량 규율."""

    def setUp(self):
        self.user = User.objects.create_user("cl", password="pass1234!")
        self.m = Merchant.objects.create(
            owner=self.user, name="협업카페", industry="cafe", location="서울")

    def test_observed_collab_duplicate_caught(self):
        """실측 회귀 — Gemini 20문 감사 #14에서 두 담당이 같은 3가지를 말했던 실제 답변.

        문자유사도 0.489로 임계(0.6)를 통과해 그대로 노출됐던 케이스다.
        (원문 축약본 — 어휘 분포는 보존)
        """
        a = ("부정 리뷰는 사장님께 속상한 일이지만, '연남살롱'의 온라인 평판을 관리하고 개선하는 "
             "중요한 기회가 될 수 있습니다.\n"
             "1. 신속하고 정중한 답변: 부정적인 리뷰에도 최대한 빨리, 정중하고 사과하는 태도로 "
             "답변하는 것이 중요합니다. 이는 다른 잠재 고객들에게 '연남살롱'이 고객의 의견을 "
             "소중히 여기고 문제 해결에 적극적이라는 인상을 줍니다.\n"
             "2. 구체적인 해결 노력 제안: 단순히 사과로 끝내지 않고, 문제 해결을 위한 구체적인 "
             "노력이나 제안을 함께 언급하는 것이 좋습니다.")
        b = ("부정 리뷰는 '연남살롱'에게 개선의 기회이며, 신속하고 현명한 대응이 중요합니다 [M13].\n"
             "1. 신속하고 정중한 답변: 부정적인 리뷰에는 최대한 빨리, 진심으로 사과하는 태도로 "
             "답변하는 것이 좋습니다. 다른 고객들에게 '연남살롱'이 고객의 의견을 소중히 여긴다는 "
             "인상을 줄 수 있습니다 [M13].\n"
             "2. 구체적인 해결 노력 제안 및 오프라인 대화 유도: 단순히 사과에 그치지 않고, "
             "불편하셨던 점을 해결해드리기 위해 노력하겠습니다.")
        self.assertTrue(_is_duplicate(a, b))

    def test_jaccard_branch_catches_low_char_similarity(self):
        """문자유사도가 임계 아래여도 어휘가 겹치면 잡는다(자카드 분기 단독 검증).

        실측 #14 전문의 프로필이 이 구간(문자 0.489 / 자카드 0.348)이었다.
        """
        a = ("리뷰에 신속하게 답변하고 구체적인 개선 노력을 제안하세요. "
             "매장으로 초대해 직접 소통하면 오해를 풀 수 있습니다.")
        b = ("구체적인 개선 노력을 제안하고 리뷰에 신속하게 답변하는 것이 좋습니다. "
             "직접 소통하면 오해를 풀 수 있으니 매장으로 초대해 보세요. 진심이 전달됩니다.")
        self.assertLess(SequenceMatcher(None, a, b).ratio(), DUPLICATE_RATIO)
        self.assertTrue(_is_duplicate(a, b))

    def test_full_paraphrase_is_a_known_limitation(self):
        """정직 고지 — 어휘가 완전히 갈리는 전면 재서술은 이 방식으로 못 잡는다.

        이 경우의 1차 방어는 코드가 아니라 프롬프트의 '각도 분리'(협업 지시)다.
        임계를 더 낮추면 서로 다른 주제까지 중복으로 오판하므로 낮추지 않는다.
        """
        a = "부정 리뷰에는 최대한 빨리 정중하게 답변하세요."
        b = "악평에 신속히 사과하는 태도로 응대하십시오."
        self.assertFalse(_is_duplicate(a, b))   # 잡히지 않는다 — 알려진 한계

    def test_different_topics_not_duplicate(self):
        """주제가 다르면 중복이 아니다(오탐 방지)."""
        a = "신규 손님을 늘리려면 플레이스 노출과 리뷰 확보가 먼저입니다."
        b = "객단가는 세트 구성과 업셀링으로 올릴 수 있습니다."
        self.assertFalse(_is_duplicate(a, b))

    def test_citation_tokens_ignored_in_comparison(self):
        """인용 번호 차이만으로 중복 판정이 흔들리지 않는다."""
        a = "재방문율을 높이려면 적립 설계가 중요합니다 [M17]."
        b = "재방문율을 높이려면 적립 설계가 중요합니다 [M19]."
        self.assertTrue(_is_duplicate(a, b))

    def test_collab_prompt_demands_distinct_angle(self):
        """협업 2번째 담당에게 '표현만 바꿔 반복 금지'와 자기 KPI 각도를 지시한다."""
        s = _system("ret", self.m, self.m.baseline_card(), collab="🎣 획득: 리뷰에 빨리 답하세요")
        self.assertIn("표현만 바꿔 반복", s)
        self.assertIn("덧붙일 내용은 없습니다", s)

    def test_length_rules_present(self):
        """분량 상한이 글자수로 명시된다(문장 수 지시는 무시됐다)."""
        self.assertIn("400자 이내", _system("cvr", self.m, self.m.baseline_card()))
        self.assertIn("600자 이내", _system("fugu", self.m, self.m.baseline_card()))

    def test_token_ceiling_per_persona(self):
        """프롬프트가 무시될 때의 최후 방어선 — 담당별 출력 상한."""
        self.assertEqual(_max_tokens_for("cvr"), 512)
        self.assertEqual(_max_tokens_for("fugu"), 700)
        self.assertEqual(_max_tokens_for("cora"), CHAT_MAX_TOKENS * 3)  # 체크리스트형 유지
        self.assertLess(_max_tokens_for("acq"), CHAT_MAX_TOKENS)        # 이전보다 조여졌다
