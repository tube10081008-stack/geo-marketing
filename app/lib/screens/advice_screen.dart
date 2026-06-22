import 'package:flutter/material.dart';

import '../models/advice.dart';
import '../widgets/data_grade_chip.dart';

/// 첫 만남 산출물 화면: 지오의 라우팅 사유 + 페르소나별 근거인용 액션 + 크레딧 청구.
class AdviceScreen extends StatelessWidget {
  final Advice advice;
  const AdviceScreen({super.key, required this.advice});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text('${advice.merchant} · 지오 액션')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 데이터등급 + provider/크레딧 요약
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Text('종합 데이터등급  '),
                      if (advice.overallGrade != null)
                        DataGradeChip(grade: advice.overallGrade!),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '엔진: ${advice.provider}  ·  라우터: ${advice.router}',
                    style: theme.textTheme.bodySmall,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '이번 분석 ${advice.credits} 크레딧 차감  '
                    '(원가 \$${advice.rawCostUsd.toStringAsFixed(5)})',
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),

          // 라우팅 사유(왜 이 페르소나를 깨웠나)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
            child: Text('지오 라우팅', style: theme.textTheme.titleMedium),
          ),
          ...advice.reasons.entries.map(
            (e) => ListTile(
              dense: true,
              leading: const Icon(Icons.route, size: 20),
              title: Text(e.value),
            ),
          ),
          const Divider(height: 24),

          // 페르소나별 액션
          ...advice.actions.map((a) => _ActionCard(action: a)),

          if (advice.disclaimer != null) ...[
            const SizedBox(height: 8),
            Text(
              '⚠️ ${advice.disclaimer}',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.outline),
            ),
          ],
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  final PersonaAction action;
  const _ActionCard({required this.action});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(action.label, style: theme.textTheme.titleMedium),
                ),
                Text(action.kpi, style: theme.textTheme.labelSmall),
              ],
            ),
            const SizedBox(height: 8),
            Text(action.action, style: theme.textTheme.bodyMedium),
            const SizedBox(height: 10),
            // 근거 인용 [M#] — 출처를 댈 수 있다는 신뢰 시그널
            Wrap(
              spacing: 6,
              children: action.citations
                  .map((m) => Chip(
                        label: Text(m, style: const TextStyle(fontSize: 11)),
                        visualDensity: VisualDensity.compact,
                        materialTapTargetSize:
                            MaterialTapTargetSize.shrinkWrap,
                      ))
                  .toList(),
            ),
            if (action.generatorModel.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('gen: ${action.generatorModel}',
                    style: theme.textTheme.labelSmall
                        ?.copyWith(color: theme.colorScheme.outline)),
              ),
          ],
        ),
      ),
    );
  }
}
