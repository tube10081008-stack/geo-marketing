import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/merchant_intake.dart';
import '../models/advice.dart';

/// 마케팅 오케스트라 백엔드 REST 클라이언트.
///
/// Base URL 주입: `--dart-define=API_BASE_URL=https://.../api/v1`
/// - Android 에뮬레이터에서 로컬 백엔드: http://10.0.2.2:8000/api/v1
/// - iOS 시뮬레이터/데스크톱/웹: http://localhost:8000/api/v1
class ApiClient {
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  final http.Client _http;
  ApiClient({http.Client? client}) : _http = client ?? http.Client();

  Map<String, String> get _jsonHeaders => {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  dynamic _decode(http.Response res) {
    if (res.statusCode >= 200 && res.statusCode < 300) {
      if (res.body.isEmpty) return null;
      return jsonDecode(utf8.decode(res.bodyBytes));
    }
    throw ApiException(res.statusCode, utf8.decode(res.bodyBytes));
  }

  /// POST /onboarding/merchants/ → 업체+3 베이스라인 통합 등록. 반환: merchant id.
  Future<int> createMerchant(MerchantIntake intake) async {
    final res = await _http.post(
      _uri('/onboarding/merchants/'),
      headers: _jsonHeaders,
      body: jsonEncode(intake.toJson()),
    );
    final data = _decode(res) as Map<String, dynamic>;
    return data['id'] as int;
  }

  /// POST /agents/advise/ → 지오 라우팅+근거인용 액션+크레딧 청구.
  Future<Advice> advise(int merchantId, {String question = ''}) async {
    final res = await _http.post(
      _uri('/agents/advise/'),
      headers: _jsonHeaders,
      body: jsonEncode({
        'merchant_id': merchantId,
        if (question.isNotEmpty) 'question': question,
      }),
    );
    return Advice.fromJson(_decode(res) as Map<String, dynamic>);
  }

  void dispose() => _http.close();
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);

  @override
  String toString() => 'ApiException($statusCode): $body';
}
