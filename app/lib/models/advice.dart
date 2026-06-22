/// 지오 에이전트 응답 모델 (agents.orchestrator.run_geo 출력).

class PersonaAction {
  final String label; // "🎣 획득"
  final String kpi;
  final String action;
  final List<String> citations; // ["M11","M15","M1"]
  final String generatorModel;

  PersonaAction({
    required this.label,
    required this.kpi,
    required this.action,
    required this.citations,
    required this.generatorModel,
  });

  factory PersonaAction.fromJson(Map<String, dynamic> j) => PersonaAction(
        label: j['label'] as String? ?? '',
        kpi: j['kpi'] as String? ?? '',
        action: j['action'] as String? ?? '',
        citations:
            (j['citations'] as List<dynamic>? ?? []).map((e) => '$e').toList(),
        generatorModel: j['generator_model'] as String? ?? '',
      );
}

class Advice {
  final String merchant;
  final String? overallGrade;
  final List<String> wokePersonas;
  final Map<String, String> reasons;
  final String router;
  final List<PersonaAction> actions;
  final String provider;
  final int credits;
  final double rawCostUsd;
  final String? disclaimer;

  Advice({
    required this.merchant,
    required this.overallGrade,
    required this.wokePersonas,
    required this.reasons,
    required this.router,
    required this.actions,
    required this.provider,
    required this.credits,
    required this.rawCostUsd,
    required this.disclaimer,
  });

  factory Advice.fromJson(Map<String, dynamic> j) {
    final routing = (j['routing'] as Map<String, dynamic>?) ?? {};
    final billing = (j['billing'] as Map<String, dynamic>?) ?? {};
    return Advice(
      merchant: j['merchant'] as String? ?? '',
      overallGrade: j['overall_data_grade'] as String?,
      wokePersonas: (routing['woke_personas'] as List<dynamic>? ?? [])
          .map((e) => '$e')
          .toList(),
      reasons: (routing['reasons'] as Map<String, dynamic>? ?? {})
          .map((k, v) => MapEntry(k, '$v')),
      router: routing['router'] as String? ?? '',
      actions: (j['actions'] as List<dynamic>? ?? [])
          .map((e) => PersonaAction.fromJson(e as Map<String, dynamic>))
          .toList(),
      provider: billing['provider'] as String? ?? '',
      credits: (billing['credits_charged'] as num?)?.toInt() ?? 0,
      rawCostUsd: (billing['raw_cost_usd'] as num?)?.toDouble() ?? 0,
      disclaimer: j['disclaimer'] as String?,
    );
  }
}
