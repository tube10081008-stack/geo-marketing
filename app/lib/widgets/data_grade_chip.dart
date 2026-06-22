import 'package:flutter/material.dart';

/// 데이터 신뢰등급 칩(A/B/C/D). 낮을수록 추정 비중↑ → 색으로 경고.
class DataGradeChip extends StatelessWidget {
  final String grade;
  const DataGradeChip({super.key, required this.grade});

  Color _color() {
    switch (grade) {
      case 'A':
        return const Color(0xFF2E7D5B); // 연동: 신뢰
      case 'B':
        return const Color(0xFF6BA368);
      case 'C':
        return const Color(0xFFE0A100); // 수기: 주의
      default:
        return const Color(0xFFC0392B); // D 추정: 경고
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: _color().withOpacity(0.12),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _color()),
      ),
      child: Text(
        grade,
        style: TextStyle(color: _color(), fontWeight: FontWeight.bold),
      ),
    );
  }
}
