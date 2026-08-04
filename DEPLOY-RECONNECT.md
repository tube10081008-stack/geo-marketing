# 🔌 배포 가이드 — Fly.io(앱) + Neon(DB) + Vercel(프런트)

Render 무료 티어의 두 문제를 피하기 위한 구성이다.

| Render 무료의 문제 | 이 구성의 해결 |
|---|---|
| 15분 미사용 시 스핀다운 → 첫 요청 30초+ | Fly 머신을 끄지 않는다(`auto_stop_machines = false`) |
| 무료 DB가 **30일 후 만료 → 14일 유예 → 데이터 삭제** | Neon 무료는 **만료 없음** |

**애플리케이션 코드 변경은 없다.** 설정이 모두 환경변수 구동이라 `DATABASE_URL` 하나로 DB가 갈리고,
`Dockerfile`이 현재 코드를 그대로 실행한다(딥리포트의 백그라운드 스레드도 정상 동작).

> ⚠️ **키는 절대 코드·저장소·채팅에 넣지 않는다.** `fly secrets`와 대시보드에만.
> 커밋 전 `./scripts/check-secrets.sh`.

---

## 1) Neon — PostgreSQL (먼저 한다)

DB 삭제 시계를 멈추는 게 급하다. 앱을 어디에 두든 이건 해야 한다.

1. [neon.com](https://neon.com) 가입 → 프로젝트 생성 (리전: **AWS ap-northeast-1 / Tokyo**)
2. 연결 문자열 복사. **Pooled connection**이 아니라 **Direct connection**을 쓴다
   (Fly는 상시 프로세스라 `conn_max_age=600` 연결 재사용이 이득이다. 풀러는 서버리스용)
   ```
   postgresql://<user>:<pw>@<host>.ap-northeast-1.aws.neon.tech/<db>?sslmode=require
   ```
3. 무료 한도: 3 GiB / 브랜치. 만료 없음. 단, 한도 초과 시 다음 결제월까지 컴퓨트가 정지된다

### 기존 Render DB에 살릴 데이터가 있다면 (이번 이전에서는 건너뜀)

> 이번 이전은 **데이터를 살리지 않기로 결정**했다(테스트 데이터뿐). 아래는 나중에
> 참고용 — 운영 데이터가 쌓인 뒤 다시 옮길 일이 생기면 이 절차를 쓴다.

**만료·삭제 전에** 반드시 먼저 뜬다:

```bash
# Render 대시보드에서 External Database URL 복사
pg_dump "postgresql://<render-url>" -Fc -f geo-backup.dump

# Neon으로 복원
pg_restore -d "postgresql://<neon-url>?sslmode=require" --no-owner --no-acl geo-backup.dump
```

데이터가 없거나 버려도 되면 이 단계를 건너뛴다 — Fly 배포 시 `release_command`가
`migrate`를 돌려 스키마를 새로 만든다.

---

## 2) Fly.io — 백엔드 API

`Dockerfile`과 `fly.toml`은 **저장소 루트**에 있다(빌드 컨텍스트가 루트여야
2단 근거 `references.md`가 이미지에 포함된다).

```bash
# 설치 후 로그인
fly auth login

# 앱 생성 — 기존 fly.toml을 쓰므로 재설정하지 않는다
fly launch --no-deploy --copy-config --name geo-fugu-api --region nrt
```

`fly.toml`에는 `app = "geo-fugu-api"`가 들어 있다. 이 이름이 이미 선점됐다면
`fly launch`가 알려주니, 다른 이름으로 만든 뒤 `fly.toml`과 `web/index.html`의
`DEFAULT` 상수를 그 이름으로 맞춘다.

### 비밀값 주입

```bash
fly secrets set \
  DJANGO_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')" \
  DATABASE_URL='postgresql://...neon.tech/...?sslmode=require' \
  GEMINI_API_KEY='<Google AI Studio 키>' \
  DJANGO_CSRF_TRUSTED_ORIGINS='https://geo-fugu-api.fly.dev'
# CORS_ALLOWED_ORIGINS 는 3단계(프런트 도메인 확보) 후에 넣는다
```

### 배포

```bash
fly deploy          # release_command 가 migrate 를 먼저 돌린다(실패 시 배포 중단)
fly logs            # 기동 확인
```

확인: `https://geo-fugu-api.fly.dev/api/v1/health/` → `{"status":"ok","provider":"gemini"}`
- `provider`가 `stub`이면 **`GEMINI_API_KEY`가 안 들어간 것**(데모 문구만 나오고 과금도 안 됨)

### 비용 감각

`shared-cpu-1x` / 512MB 1대 상시 가동 기준 월 몇 달러 수준.
**`auto_stop_machines = false`가 콜드스타트 제거의 핵심이므로 켜지 말 것** — 켜면 이사한 의미가 없다.

---

## 3) Vercel — 프런트 + 게스트 API

1. Add New Project → 이 저장소 → **Root Directory = `web`**
2. Framework Preset: **Other** (빌드 명령 없음)
3. 게스트 모드에서 실제 생성이 필요하면 Vercel 환경변수에도 `GEMINI_API_KEY` 추가

`web/api/*.py`는 표준 라이브러리만 쓰는 self-contained 함수라 별도 의존성이 없다.

---

## 4) 서로 연결

### 백엔드 ← 프런트 도메인 허용
```bash
fly secrets set CORS_ALLOWED_ORIGINS='https://<프런트도메인>.vercel.app'
```
미설정이면 `CORS_ALLOW_ALL_ORIGINS = True`로 동작한다(PoC 편의). **운영에서는 반드시 지정.**

### 프런트 → 백엔드 주소
기본값은 이미 `https://geo-fugu-api.fly.dev`로 맞춰져 있다.
다른 이름으로 앱을 만들었다면 **프런트 재배포 없이** 이렇게 바꾼다:

```
https://<프런트도메인>/?api=https://geo-fugu-api.fly.dev
```

한 번 열면 `localStorage`에 저장되어 유지된다(https만 허용, 잘못된 값은 기본값으로 폴백).
영구 변경은 `web/index.html`의 `DEFAULT` 상수를 고쳐 커밋.

---

## 5) 확인 체크리스트

- [ ] `/api/v1/health/` → `provider: gemini`
- [ ] 프런트 사이드바 **엔진**이 "Gemini 라이브"(회색 "데모" 아님)
- [ ] 회원가입 → 가입 포인트 10,000P
- [ ] 대화 1회 → `[M#]` 인용 칩 표시 + 포인트 차감
- [ ] **두 번째 요청이 즉시 응답**(콜드스타트 없음 — 이사한 이유)
- [ ] 콘솔에 CORS 오류 없음

---

## 문제별 대처

| 증상 | 원인 | 조치 |
|---|---|---|
| 엔진이 계속 "데모(무과금)" | `GEMINI_API_KEY` 미설정 | `fly secrets list`로 확인 후 재설정 |
| 배포가 migrate에서 실패 | `DATABASE_URL` 오류/SSL 미설정 | 문자열 끝 `?sslmode=require` 확인 |
| CORS 차단 | 프런트 도메인 미등록 | `CORS_ALLOWED_ORIGINS`에 추가(스킴 포함, 끝 슬래시 없이) |
| 첫 요청이 여전히 느림 | `auto_stop_machines`가 켜짐 | `fly.toml`에서 `false` 확인 후 재배포 |
| 답변에 상세 근거가 안 붙음 | 이미지에 `references.md` 누락 | 빌드 컨텍스트가 **저장소 루트**인지 확인 |
| 401 Unauthorized | 키 폐기/만료 | 새 키 발급 후 `fly secrets set` |

---

## Render 관련 정리

`render.yaml`은 롤백용으로 남겨둔다. 다만 Render Blueprint가 남아 있으면 **자동 동기화가
옛 서비스를 계속 건드릴 수 있으니**, 이전이 끝나면 Render 쪽 Blueprint와 웹 서비스를 정리한다.
**무료 DB는 만료 시각까지 두되, 데이터 덤프를 먼저 확보한 뒤 삭제**한다.
