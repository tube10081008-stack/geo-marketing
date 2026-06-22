# 마케팅 오케스트라 — 앱 (Flutter, P1)

소상공인이 **첫 만남(기초자료 수집)** 을 진행하고 **지오 에이전트의 근거기반 액션**을 받는 모바일 앱.
백엔드: [`../backend`](../backend) · 설계: [`../onboarding-intake.md`](../onboarding-intake.md)

> ⚠️ 도박 DTx 앱(`poc/app`)과 **분리된 별도 Flutter 프로젝트**. 컨벤션(Riverpod·http·M3)은 동일.

## 실행
```bash
cd marketing-orchestra/app
flutter pub get
# 백엔드 주소 주입(미주입 시 localhost:8000)
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1   # Android 에뮬레이터
```
> 이 클라우드 환경엔 Flutter SDK가 없어 **코드 편집만** 가능. 빌드/실행은 로컬에서.

## 화면 플로우 (onboarding-intake.md §3)
```
OnboardingScreen (4스텝 Stepper)
  공통 → 🎣획득 → 💳전환 → 🔁유지   (+ 각 단계 데이터 신뢰등급 A/B/C/D)
        │ 동의 게이트(미동의 시 제출 차단)
        ▼  POST /onboarding/merchants/  →  POST /agents/advise/
AdviceScreen
  종합 데이터등급 + 엔진/라우터 + 크레딧 청구
  지오 라우팅 사유(왜 이 페르소나를 깨웠나)
  페르소나별 액션 + [M#] 근거 인용 칩 + 측정목표
```

## 구조
| 파일 | 역할 |
| --- | --- |
| `lib/models/merchant_intake.dart` | 입력 폼 → 중첩 JSON(Decimal은 문자열) |
| `lib/models/advice.dart` | 지오 응답(라우팅·액션·크레딧) |
| `lib/api/api_client.dart` | `createMerchant` / `advise` |
| `lib/state/onboarding_controller.dart` | Riverpod: 제출=등록→액션 |
| `lib/screens/onboarding_screen.dart` | 4스텝 첫 만남 |
| `lib/screens/advice_screen.dart` | 산출물(액션+인용+크레딧) |
| `lib/widgets/data_grade_chip.dart` | 등급 칩(D=경고색) |

## 설계 반영
- **데이터 신뢰등급**을 입력 단계에서 함께 수집(추정/연동 투명화)
- **개인정보 게이트**: 위탁처리 미동의 시 제출 차단
- **[M#] 인용**을 액션 카드에 칩으로 노출 = "출처를 댈 수 있다" 신뢰 시그널
- **크레딧 청구액**을 결과에 표시(계측=과금 투명성)
