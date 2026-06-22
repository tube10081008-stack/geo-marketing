import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/advice.dart';
import '../models/merchant_intake.dart';
import '../state/onboarding_controller.dart';
import 'advice_screen.dart';

/// 첫 만남(기초자료 수집) 플로우 — onboarding-intake.md §3.
///
/// 4단계 스텝: 공통 → 획득 → 전환 → 유지. 마지막에 지오에게 액션 요청.
/// 설문폭격 대신 단계별 최소셋 + 각 페르소나 데이터등급을 함께 받는다.
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  final _intake = MerchantIntake();
  int _step = 0;

  // 컨트롤러(텍스트 입력)
  final _name = TextEditingController();
  final _location = TextEditingController();
  final _revenue = TextEditingController();
  final _goal = TextEditingController();
  final _question = TextEditingController();
  final _placeClicks = TextEditingController();
  final _reviewCount = TextEditingController();
  final _avgRating = TextEditingController();
  final _aov = TextEditingController();
  final _convRate = TextEditingController();
  final _revisit = TextEditingController();
  final _nps = TextEditingController();

  @override
  void dispose() {
    for (final c in [
      _name, _location, _revenue, _goal, _question, _placeClicks,
      _reviewCount, _avgRating, _aov, _convRate, _revisit, _nps,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  int? _toInt(TextEditingController c) =>
      c.text.trim().isEmpty ? null : int.tryParse(c.text.trim());

  String? _decStr(TextEditingController c) =>
      c.text.trim().isEmpty ? null : c.text.trim();

  void _collect() {
    _intake
      ..name = _name.text.trim()
      ..location = _location.text.trim()
      ..monthlyRevenue = _toInt(_revenue)
      ..growthGoalPct = _decStr(_goal)
      ..placeClicks = _toInt(_placeClicks)
      ..reviewCount = _toInt(_reviewCount)
      ..avgRating = _decStr(_avgRating)
      ..aov = _toInt(_aov)
      ..visitToPurchaseRate = _decStr(_convRate)
      ..revisitRate = _decStr(_revisit)
      ..nps = _toInt(_nps);
  }

  Future<void> _submit() async {
    _collect();
    if (_intake.name.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('상호를 입력해 주세요.')),
      );
      setState(() => _step = 0);
      return;
    }
    if (!_intake.consent) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('고객데이터 위탁처리 동의가 필요합니다(개인정보 게이트).')),
      );
      setState(() => _step = 0);
      return;
    }
    await ref
        .read(onboardingControllerProvider.notifier)
        .submit(_intake, question: _question.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(onboardingControllerProvider);

    // 결과가 도착하면 액션 화면으로 이동 / 에러는 스낵바
    ref.listen<AsyncValue<Advice?>>(onboardingControllerProvider, (prev, next) {
      next.whenOrNull(
        data: (advice) {
          if (advice != null) {
            ref.read(onboardingControllerProvider.notifier).reset();
            Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => AdviceScreen(advice: advice),
            ));
          }
        },
        error: (e, _) {
          final msg = e is ApiException ? '오류 ${e.statusCode}' : '$e';
          ScaffoldMessenger.of(context)
              .showSnackBar(SnackBar(content: Text('전송 실패: $msg')));
        },
      );
    });

    final loading = state.isLoading;

    return Scaffold(
      appBar: AppBar(title: const Text('첫 만남 — 기초자료 수집')),
      body: Stepper(
        currentStep: _step,
        onStepTapped: loading ? null : (i) => setState(() => _step = i),
        onStepContinue: loading
            ? null
            : () {
                if (_step < 3) {
                  setState(() => _step += 1);
                } else {
                  _submit();
                }
              },
        onStepCancel: loading || _step == 0
            ? null
            : () => setState(() => _step -= 1),
        controlsBuilder: (context, details) => Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Row(
            children: [
              FilledButton(
                onPressed: details.onStepContinue,
                child: loading && _step == 3
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(_step < 3 ? '다음' : '지오에게 액션 받기'),
              ),
              const SizedBox(width: 8),
              if (_step > 0)
                TextButton(
                    onPressed: details.onStepCancel, child: const Text('이전')),
            ],
          ),
        ),
        steps: [
          _commonStep(),
          _acqStep(),
          _cvrStep(),
          _retStep(),
        ],
      ),
    );
  }

  Step _commonStep() => Step(
        isActive: _step >= 0,
        title: const Text('공통'),
        subtitle: const Text('업종·상권·매출·목표·동의'),
        content: Column(
          children: [
            _text(_name, '상호', hint: '예: 지오네 고깃집'),
            _industryDropdown(),
            _text(_location, '위치·상권', hint: '예: 서울 마포 합정역'),
            _text(_revenue, '월 매출(원)', number: true, hint: '예: 30000000'),
            _text(_goal, '90일 목표 성장률(%)', number: true, hint: '예: 15'),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('고객데이터 위탁처리 동의'),
              subtitle: const Text('거래로그(가명) 활용 — 미동의 시 RFM 분석 불가'),
              value: _intake.consent,
              onChanged: (v) => setState(() => _intake.consent = v),
            ),
          ],
        ),
      );

  Step _acqStep() => Step(
        isActive: _step >= 1,
        title: const Text('🎣 획득'),
        subtitle: const Text('노출·리뷰·평점'),
        content: Column(
          children: [
            _text(_placeClicks, '플레이스 클릭(월)', number: true),
            _text(_reviewCount, '리뷰 개수(채널 합)', number: true),
            _text(_avgRating, '평균 평점(0~5)', number: true, hint: '예: 4.1'),
            _gradeDropdown('획득', _intake.acqGrade,
                (g) => setState(() => _intake.acqGrade = g)),
          ],
        ),
      );

  Step _cvrStep() => Step(
        isActive: _step >= 2,
        title: const Text('💳 전환'),
        subtitle: const Text('객단가·전환율'),
        content: Column(
          children: [
            _text(_aov, '객단가 AOV(원)', number: true, hint: '예: 28000'),
            _text(_convRate, '방문→구매 전환율(%)', number: true, hint: '예: 55'),
            _gradeDropdown('전환', _intake.cvrGrade,
                (g) => setState(() => _intake.cvrGrade = g)),
          ],
        ),
      );

  Step _retStep() => Step(
        isActive: _step >= 3,
        title: const Text('🔁 유지'),
        subtitle: const Text('재방문·NPS + 질문(선택)'),
        content: Column(
          children: [
            _text(_revisit, '재방문율(%)', number: true, hint: '예: 30'),
            _text(_nps, 'NPS(-100~100)', number: true, hint: '예: 22'),
            _gradeDropdown('유지', _intake.retGrade,
                (g) => setState(() => _intake.retGrade = g)),
            _text(_question, '지오에게 한마디(선택)',
                hint: '예: 신규 손님을 더 끌고 싶어요'),
          ],
        ),
      );

  Widget _text(TextEditingController c, String label,
      {bool number = false, String? hint}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: TextField(
        controller: c,
        keyboardType: number
            ? const TextInputType.numberWithOptions(decimal: true)
            : TextInputType.text,
        inputFormatters: number
            ? [FilteringTextInputFormatter.allow(RegExp(r'[0-9.]'))]
            : null,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: const OutlineInputBorder(),
          isDense: true,
        ),
      ),
    );
  }

  Widget _industryDropdown() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: DropdownButtonFormField<String>(
          value: _intake.industry,
          decoration: const InputDecoration(
            labelText: '업종',
            border: OutlineInputBorder(),
            isDense: true,
          ),
          items: kIndustries
              .map((i) =>
                  DropdownMenuItem(value: i.value, child: Text(i.label)))
              .toList(),
          onChanged: (v) => setState(() => _intake.industry = v ?? 'food'),
        ),
      );

  Widget _gradeDropdown(
          String persona, String value, ValueChanged<String> onChanged) =>
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: DropdownButtonFormField<String>(
          value: value,
          decoration: InputDecoration(
            labelText: '$persona 데이터 신뢰등급',
            border: const OutlineInputBorder(),
            isDense: true,
          ),
          items: kDataGrades
              .map((g) =>
                  DropdownMenuItem(value: g.value, child: Text(g.label)))
              .toList(),
          onChanged: (v) => onChanged(v ?? 'C'),
        ),
      );
}
