# 🔌 호스팅 재연결 가이드 (Vercel + Render)

모노레포에서 분리된 뒤 끊긴 배포를 다시 잇는 절차. **대시보드 작업은 사장님 몫**이고,
저장소 쪽 준비는 모두 끝나 있다.

> ⚠️ **API 키는 대시보드에만 입력한다.** 코드·저장소·채팅에 절대 넣지 않는다.
> 커밋 전 `./scripts/check-secrets.sh`로 점검.

---

## 1) Render — 백엔드 API

`render.yaml`(Blueprint)이 이미 이 저장소 구조에 맞춰져 있다: `rootDir: backend`, `branch: main`.

1. Render 대시보드 → **New** → **Blueprint**
2. 저장소 `tube10081008-stack/geo-marketing` 선택 → `render.yaml` 자동 인식
3. 생성되는 리소스
   - 웹 서비스 `geo-marketing-api` (gunicorn + whitenoise)
   - PostgreSQL `geo-marketing-db` (`DATABASE_URL` 자동 연결)
4. **환경변수 직접 입력** (`sync: false`라 대시보드에서만 넣는다)
   | 키 | 값 |
   |---|---|
   | `GEMINI_API_KEY` | Google AI Studio 키 |
   | `CORS_ALLOWED_ORIGINS` | *2단계에서 Vercel 도메인 확보 후 입력* |
5. 배포 후 확인: `https://<서비스>.onrender.com/api/v1/health/`
   → `{"status":"ok","provider":"gemini"}`
   - `provider`가 `stub`이면 **키가 안 들어간 것** (데모 문구만 나오고 과금도 안 됨)

> 무료 플랜은 콜드스타트가 있다(첫 요청 30초+). 프런트에 안내 토스트가 이미 있다.

---

## 2) Vercel — 프런트 + 게스트 API

1. Vercel → **Add New Project** → 같은 저장소 선택
2. **Root Directory = `web`** ← 이것만 맞추면 된다 (`web/vercel.json`이 서버리스 함수 설정을 갖고 있다)
3. Framework Preset: **Other** (빌드 명령 없음, 정적 + Python 함수)
4. 배포 후 도메인 확보 (예: `https://geo-marketing.vercel.app`)

`web/api/*.py`(게스트 모드)는 표준 라이브러리만 쓰는 self-contained 함수라 별도 의존성이 없다.
게스트 모드에서 실제 생성이 필요하면 **Vercel 환경변수에도** `GEMINI_API_KEY`를 넣는다.

---

## 3) 서로 연결

### 백엔드 ← 프런트 도메인 허용
Render 대시보드에서 `CORS_ALLOWED_ORIGINS`에 2단계 도메인을 입력한다(콤마 구분, 스킴 포함):

```
https://geo-marketing.vercel.app,https://geo-marketing-git-main-xxx.vercel.app
```

미설정 시 `CORS_ALLOW_ALL_ORIGINS = True`로 동작한다(PoC 편의). **운영에서는 반드시 지정할 것.**

### 프런트 → 백엔드 주소
기본값은 `https://geo-marketing-api.onrender.com`이다.
Render 서비스명을 다르게 만들었다면 **재배포 없이** 바꿀 수 있다:

```
https://<프런트도메인>/?api=https://<내서비스>.onrender.com
```

한 번 열면 `localStorage`에 저장되어 이후 방문에도 유지된다.
(https만 허용 — 잘못된 값은 조용히 기본값으로 되돌아간다.)

영구적으로 바꾸려면 `web/index.html`의 `DEFAULT` 상수를 수정해 커밋한다.

---

## 4) 연결 확인 체크리스트

- [ ] `/api/v1/health/`가 `provider: gemini` 반환
- [ ] 프런트 사이드바 **엔진** 표시가 "Gemini 라이브"(회색 "데모"가 아님)
- [ ] 회원가입 → 가입 포인트 10,000P 표시
- [ ] 대화 1회 → 답변에 `[M#]` 인용 칩이 뜨고 포인트가 차감됨
- [ ] 브라우저 콘솔에 CORS 오류 없음

---

## 문제별 대처

| 증상 | 원인 | 조치 |
|---|---|---|
| 엔진이 계속 "데모(무과금)" | `GEMINI_API_KEY` 미설정/오타 | Render 환경변수 확인 → 재배포 |
| 콘솔에 CORS 차단 | `CORS_ALLOWED_ORIGINS`에 프런트 도메인 없음 | 도메인 추가(스킴 포함, 끝 슬래시 없이) |
| 첫 요청만 매우 느림 | Render 무료 플랜 콜드스타트 | 정상 동작 |
| 로그인은 되는데 저장이 안 됨 | DB 미연결 | Blueprint의 `DATABASE_URL` 연결 확인 |
| 401 Unauthorized | 키 폐기/만료 | 새 키 발급 후 대시보드에서 교체 |
