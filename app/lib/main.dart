import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'screens/onboarding_screen.dart';

void main() {
  runApp(const ProviderScope(child: GeoApp()));
}

/// 마케팅 오케스트라 앱 루트. 첫 화면 = 온보딩(첫 만남) 플로우.
class GeoApp extends StatelessWidget {
  const GeoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '지오 — 소상공인 마케팅',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2E7D5B), // 성장·신뢰의 그린 톤
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF7F9F8),
        fontFamily: 'Roboto',
      ),
      home: const OnboardingScreen(),
    );
  }
}
