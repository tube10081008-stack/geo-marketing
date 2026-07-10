# 🚀 백엔드 배포 (Render) — 계정·영속 API

마케팅 오케스트라 백엔드(Django + DRF 토큰인증 + PostgreSQL)를 Render에 올린다.
> 정적/서버리스 프런트(Vercel)와 달리 이쪽은 **DB가 필요한 상태 보존 서버**라 Render(또는 Railway/Fly)가 맞다.

## 방법 A — Render 대시보드 (수동, 권장)
1. **New → Web Service** → 이 저장소 연결
2. 설정:
   - **Branch**: `claude/wonderful-lovelace-54xws3`
   - **Root Directory**: `marketing-orchestra/backend`
   - **Runtime**: `Python`
   - **Build Command**:
     ```
     pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
     ```
   - **Start Command**: `gunicorn config.wsgi`
3. **New → PostgreSQL**(무료) 생성 → 그 DB의 **Internal Database URL** 복사
4. 웹서비스 **Environment**에 변수 추가:
   | 키 | 값 |
   | --- | --- |
   | `DATABASE_URL` | (위 Postgres Internal URL) |
   | `DJANGO_SECRET_KEY` | 랜덤 문자열(Generate) |
   | `DJANGO_DEBUG` | `False` |
   | `DJANGO_ALLOWED_HOSTS` | `.onrender.com,localhost,127.0.0.1` |
   | `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://<서비스>.onrender.com` |
   | `CORS_ALLOWED_ORIGINS` | `https://urge-surfing.vercel.app` |
   | `GEMINI_API_KEY` | (새 Gemini 키 — 코드/채팅 금지) |
5. **Create Web Service** → 배포 완료 후 `https://<서비스>.onrender.com/api/v1/` 사용
6. (선택) 관리자: Shell에서 `python manage.py createsuperuser`

## 방법 B — Blueprint (render.yaml) ✅ 반영 완료
루트 `render.yaml`에 `geo-marketing-api` 서비스 + `geo-marketing-db`가 **이미 추가돼 있다.**
- 이 저장소를 Blueprint로 이미 연결해뒀다면(도박 PoC 배포 시): Render 대시보드 → Blueprints → **Sync/Apply** 승인만 하면 생성된다.
- 처음이라면: **New → Blueprint → 이 저장소 → 브랜치 `claude/wonderful-lovelace-54xws3` → Apply**.
- Apply 후 대시보드에서 `GEMINI_API_KEY`만 직접 입력(`sync: false`라 프롬프트가 뜬다).
- ⚠️ 무료 Postgres는 계정당 1개 — `urge-surfing-db`가 이미 있으면 `geo-marketing-db` 생성이 거부될 수 있다. 그 경우 옛 DB를 삭제하거나 유료 플랜 선택.
- 헬스체크는 `/api/v1/health/`(무인증 200 — `/auth/me/`는 401이라 헬스체크에 부적합).

참고용 원본 스니펫:
```yaml
  - type: web
    name: geo-marketing-api
    runtime: python
    plan: free
    rootDir: marketing-orchestra/backend
    branch: claude/wonderful-lovelace-54xws3
    buildCommand: "pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate"
    startCommand: "gunicorn config.wsgi"
    healthCheckPath: /api/v1/health/
    envVars:
      - key: DJANGO_SECRET_KEY
        generateValue: true
      - key: DJANGO_DEBUG
        value: "False"
      - key: DJANGO_ALLOWED_HOSTS
        value: ".onrender.com,localhost,127.0.0.1"
      - key: CORS_ALLOWED_ORIGINS
        value: "https://urge-surfing.vercel.app"
      - key: GEMINI_API_KEY
        sync: false               # 대시보드에서 직접 입력(코드 미포함)
      - key: DATABASE_URL
        fromDatabase: { name: geo-marketing-db, property: connectionString }
databases:
  - name: geo-marketing-db
    plan: free
```

## API 요약 (모두 `/api/v1`)
| 메서드·경로 | 인증 | 설명 |
| --- | --- | --- |
| `POST /auth/register/` | — | 회원가입 → `{token}` |
| `POST /auth/login/` | — | 로그인 → `{token}` |
| `GET  /auth/me/` | Token | 내 정보 |
| `GET/POST /onboarding/merchants/` | Token | **자기 업체만** 조회/생성(베이스라인) |
| `GET  /onboarding/merchants/{id}/card/` | Token | 베이스라인 카드 |
| `POST /agents/advise/` | Token | 종합 액션(소유 검증) |
| `POST /agents/chat/` | Token | 세 에이전트 대화(메시지 **DB 영속**) |
| `GET  /agents/history/{merchant_id}/` | Token | 대화 이력 복원(멀티기기) |

> 인증: `Authorization: Token <키>` 헤더. 각 계정은 자기 업체·대화만 접근(소유 격리 검증됨).

## 다음(프런트 연동)
현재 Vercel 웹은 자체 서버리스(`/api/*`)+localStorage를 쓴다. 이 백엔드로 **로그인+계정 영속**을
붙이려면 프런트에 ① 로그인/회원가입 화면 ② API_BASE를 이 Render 주소로 ③ 토큰 헤더를 추가한다.
(배포 URL 나오면 바로 연동 가능.)
