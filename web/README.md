# 지오 웹 — Vercel 배포본

소상공인이 **베이스라인을 입력 → 근거기반 마케팅 액션**을 받는 웹앱.
정적 프런트(`index.html`) + Python 서버리스 함수(`/api/advise`). **DB 불필요·의존성 0**.

```
web/
├─ index.html        # 대시보드(빌드 불필요)
├─ vercel.json       # 함수 설정(maxDuration)
└─ api/
   ├─ advise.py      # POST /api/advise — 베이스라인 → 1회 종합 액션(self-contained)
   ├─ chat.py        # POST /api/chat — 에이전트 멀티턴 대화
   └─ health.py      # GET  /api/health — 게스트 엔진 상태
```

> ⚠️ **`requirements.txt`를 만들지 마세요.** 외부 패키지가 0개라 필요 없을 뿐 아니라,
> 파일이 존재하면 Vercel이 `web/`을 **단일 Python 앱**으로 감지해
> `No python entrypoint found ... Add [tool.vercel] entrypoint` 오류로 빌드가 실패합니다.
> 파일이 없어야 `api/*.py`가 각각 독립 서버리스 함수로 잡힙니다.
> (2026-08 실제 배포 실패로 확인 — 그래서 삭제했습니다.)

## 🚀 Vercel 배포 (택1)

### A. GitHub 연동 (권장)
1. [vercel.com](https://vercel.com) → **Add New… → Project** → 이 레포 import
2. **Root Directory = `web`** 로 지정 ⭐ (중요)
   *(독립 저장소로 분리되며 경로가 한 단계 올라왔다 — 옛 `marketing-orchestra/web` 아님)*
3. **Environment Variables**에 추가:
   - `GEMINI_API_KEY = <당신의 Gemini 키>`  ← 코드/채팅 말고 여기에만
   - (선택) `GEO_GEMINI_GENERATOR`, `GEO_GEMINI_ROUTER`, `GEO_CREDIT_COST_USD`
4. **Deploy**. 끝. (정적 `index.html` + `/api/*.py` Python 함수 자동 감지)

### B. CLI
```bash
cd web
npm i -g vercel
vercel            # 첫 배포(프리뷰)
vercel env add GEMINI_API_KEY   # 키 입력(프롬프트)
vercel --prod     # 운영 배포
```

## 동작
- `GEMINI_API_KEY` 있으면 **실 Gemini**(라우터=2.5-flash-lite / 생성=2.5-flash, thinking off).
- 없으면 결정적 **StubProvider**(키 없이도 폼·인용·계측 데모 동작).
- 응답에 `billing.provider`로 실연동 여부 투명 표시.

## 설계 일관성 (백엔드와 동일)
- 게이트 라우팅(약한 KPI만 페르소나 깨움) · [M#] 출처 인용 · 데이터등급(D=경고) · 계측=과금.

## 보안
- 키는 **Vercel 환경변수**로만. 코드·git·프런트에 절대 포함 금지(확인됨).
- 노출된 테스트 키는 즉시 폐기 후 새 키 사용.

> 참고: 전체 Django 백엔드(DB·온보딩 영속화)는 [`../backend`](../backend). 이 웹본은 Vercel-네이티브 *stateless 데모/영업용*으로, 베이스라인을 직접 받아 액션만 산출한다.
