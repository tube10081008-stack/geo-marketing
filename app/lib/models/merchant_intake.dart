/// 온보딩 입력 폼 모델 (onboarding-intake.md §2 최소셋).
///
/// Decimal 필드(평점·전환율·재방문율·목표성장률)는 DRF DecimalField에 맞춰
/// 문자열로 전송한다. 정수 필드는 int. 빈 값은 전송에서 제외.
class MerchantIntake {
  // 공통 §2.0
  String name;
  String industry; // food|beauty|retail|cafe|service|etc
  String location;
  int? monthlyRevenue;
  String? growthGoalPct; // "15.0"
  bool consent;

  // 🎣 획득 §2.1
  int? placeClicks;
  int? reviewCount;
  String? avgRating; // "4.1" (0~5)
  String acqGrade; // A|B|C|D

  // 💳 전환 §2.2
  int? aov;
  String? visitToPurchaseRate; // "55.0"
  String cvrGrade;

  // 🔁 유지 §2.3
  String? revisitRate; // "30.0"
  int? nps;
  String retGrade;

  MerchantIntake({
    this.name = '',
    this.industry = 'food',
    this.location = '',
    this.monthlyRevenue,
    this.growthGoalPct,
    this.consent = false,
    this.placeClicks,
    this.reviewCount,
    this.avgRating,
    this.acqGrade = 'C',
    this.aov,
    this.visitToPurchaseRate,
    this.cvrGrade = 'C',
    this.revisitRate,
    this.nps,
    this.retGrade = 'C',
  });

  Map<String, dynamic> toJson() => {
        'name': name,
        'industry': industry,
        'location': location,
        if (monthlyRevenue != null) 'monthly_revenue': monthlyRevenue,
        if (growthGoalPct != null && growthGoalPct!.isNotEmpty)
          'growth_goal_pct': growthGoalPct,
        'consent_data_processing': consent,
        'acquisition': {
          if (placeClicks != null) 'place_clicks': placeClicks,
          if (reviewCount != null) 'review_count': reviewCount,
          if (avgRating != null && avgRating!.isNotEmpty) 'avg_rating': avgRating,
          'data_grade': acqGrade,
        },
        'conversion': {
          if (aov != null) 'aov': aov,
          if (visitToPurchaseRate != null && visitToPurchaseRate!.isNotEmpty)
            'visit_to_purchase_rate': visitToPurchaseRate,
          'data_grade': cvrGrade,
        },
        'retention': {
          if (revisitRate != null && revisitRate!.isNotEmpty)
            'revisit_rate': revisitRate,
          if (nps != null) 'nps': nps,
          'data_grade': retGrade,
        },
      };
}

/// 업종 선택지(백엔드 Industry.choices와 일치).
const List<({String value, String label})> kIndustries = [
  (value: 'food', label: '외식/음식점'),
  (value: 'beauty', label: '미용/뷰티'),
  (value: 'retail', label: '소매/판매'),
  (value: 'cafe', label: '카페/디저트'),
  (value: 'service', label: '생활서비스'),
  (value: 'etc', label: '기타'),
];

/// 데이터 신뢰등급(onboarding-intake.md §1).
const List<({String value, String label})> kDataGrades = [
  (value: 'A', label: 'A 연동(POS·플레이스 API)'),
  (value: 'B', label: 'B 내보내기(엑셀·정산서)'),
  (value: 'C', label: 'C 수기(사장님 입력)'),
  (value: 'D', label: 'D 추정(업종평균)'),
];
