"""업종 벤치마크 차분(diff-in-diff) — 조언→실측 루프 ⑤.

'내 지표 증가율 vs 동종업종 증가율'을 비교해 계절성·시장 전체 추세를 통제한다.
(예: 12월 매출 +10%가 내 실력인지, 연말 특수로 업종 전체가 +12%인지 구분)

정직 원칙:
- 익명 집계 — 개별 업체의 값·이름은 절대 노출하지 않는다. 반환은 중앙값과 표본수뿐.
- 표본 n < MIN_SAMPLE(10)이면 업종 값을 숨긴다(소표본 왜곡·역식별 방지). n은 공개해
  "왜 아직 안 보이는지"를 정직하게 알린다.
- 증가율 요약은 평균이 아닌 **중앙값**: 소표본에서 극단값 1건이 평균을 왜곡하는 것을
  막는다(표본이 커지면 평균 병기 검토).
"""
from datetime import timedelta
from statistics import median

from django.utils import timezone

from onboarding.models import METRIC_CHOICES, KpiSnapshot, Merchant

WINDOW_DAYS = 90     # 증가율 산출 윈도 — 주간 체크인이 쌓이는 속도 기준 한 분기
MIN_SPAN_DAYS = 14   # 첫/끝 측정이 이보다 가까우면 추세로 보지 않는다
MIN_SAMPLE = 10      # 업종 집계 공개 최소 표본(자기 자신 제외)

_METRIC_LABELS = dict(METRIC_CHOICES)


def _growth_pct(points):
    """시계열 [(측정시각, 값), ...] → 윈도 내 첫/끝 % 변화. 산출 불가면 None.

    첫 값이 0 이하이거나(0나눗셈·음수 기준), 두 점의 간격이 MIN_SPAN_DAYS 미만이면
    추세라고 말하지 않는다(정직 규칙).
    """
    if len(points) < 2:
        return None
    (t0, v0), (t1, v1) = points[0], points[-1]
    if v0 <= 0 or (t1 - t0).days < MIN_SPAN_DAYS:
        return None
    return float((v1 - v0) / v0 * 100)


def _series_by_merchant(industry, metric, since):
    """업종·지표의 윈도 내 스냅샷을 업체별 시계열로 그룹핑(쿼리 1회)."""
    rows = (KpiSnapshot.objects
            .filter(merchant__industry=industry, metric=metric, created_at__gte=since)
            .order_by("created_at")
            .values_list("merchant_id", "created_at", "value"))
    series = {}
    for mid, at, value in rows:
        series.setdefault(mid, []).append((at, value))
    return series


def benchmark_rows(merchant: Merchant, now=None):
    """업체의 벤치마크 행 목록 — 내 증가율이 산출되는 지표만.

    각 행: metric/label/mine_pct/industry_pct/diff_pct/n/hidden.
    업종 집계는 자기 자신을 제외하고 표본 n >= MIN_SAMPLE일 때만 공개(hidden=False).
    """
    now = now or timezone.now()
    since = now - timedelta(days=WINDOW_DAYS)
    out = []
    for metric, label in METRIC_CHOICES:
        series = _series_by_merchant(merchant.industry, metric, since)
        mine = _growth_pct(series.get(merchant.pk, []))
        if mine is None:
            continue  # 내 추세가 없으면 비교 자체가 성립하지 않는다
        peers = [g for mid, pts in series.items() if mid != merchant.pk
                 for g in [_growth_pct(pts)] if g is not None]
        n = len(peers)
        hidden = n < MIN_SAMPLE
        industry_pct = None if hidden else round(median(peers), 1)
        out.append({
            "metric": metric,
            "label": _METRIC_LABELS.get(metric, metric),
            "mine_pct": round(mine, 1),
            "industry_pct": industry_pct,
            "diff_pct": None if hidden else round(round(mine, 1) - industry_pct, 1),
            "n": n,
            "hidden": hidden,
        })
    return out


def benchmark_note(merchant: Merchant, now=None) -> str:
    """챗/리포트 프롬프트 주입용 요약 — 공개 가능한(n 충족) 행만, 없으면 빈 문자열."""
    lines = []
    for r in benchmark_rows(merchant, now=now):
        if r["hidden"]:
            continue
        lines.append(
            f"- {r['label']}: 내 {r['mine_pct']:+.1f}% vs 업종 중앙값 "
            f"{r['industry_pct']:+.1f}% (차분 {r['diff_pct']:+.1f}%p, 표본 n={r['n']})")
    return "\n".join(lines)
