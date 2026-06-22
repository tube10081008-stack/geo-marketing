import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../models/advice.dart';
import '../models/merchant_intake.dart';

/// API 클라이언트 provider.
final apiClientProvider = Provider<ApiClient>((ref) {
  final client = ApiClient();
  ref.onDispose(client.dispose);
  return client;
});

/// 온보딩 제출 상태: 업체 등록 → 지오 액션 호출의 결과(Advice).
final onboardingControllerProvider =
    StateNotifierProvider<OnboardingController, AsyncValue<Advice?>>((ref) {
  return OnboardingController(ref.watch(apiClientProvider));
});

class OnboardingController extends StateNotifier<AsyncValue<Advice?>> {
  OnboardingController(this._api) : super(const AsyncValue.data(null));

  final ApiClient _api;

  /// 첫 만남 제출: 베이스라인 등록 → 지오 종합 액션 요청.
  Future<void> submit(MerchantIntake intake, {String question = ''}) async {
    state = const AsyncValue.loading();
    try {
      final merchantId = await _api.createMerchant(intake);
      final advice = await _api.advise(merchantId, question: question);
      state = AsyncValue.data(advice);
    } catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }

  void reset() => state = const AsyncValue.data(null);
}
