# 지오(GEO) 백엔드 — Fly.io 배포용.
#
# Render 무료 티어의 두 문제(15분 스핀다운 콜드스타트 / 무료 DB 30일 후 삭제)를
# 피하려 상시 프로세스 플랫폼으로 옮긴다. **애플리케이션 코드 변경은 없다** —
# 이 이미지가 현재 코드를 그대로 실행하며, 딥리포트의 백그라운드 스레드
# (agents/views.py의 threading.Thread)도 정상 동작한다.
#
# 빌드 컨텍스트는 **저장소 루트**다(backend/ 아님). 2단 근거(agents/notes.py)가
# 루트의 references.md를 읽기 때문에, 그 파일을 이미지에 함께 넣어야 상세 근거가
# 살아난다. 없으면 notes.py가 조용히 1단(한 줄 색인)만 쓰도록 degradation 한다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# psycopg[binary]는 휠로 설치되어 컴파일러가 필요 없다(이미지 경량 유지).
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
# 2단 근거 코퍼스 — notes.py의 탐색 경로(cwd/references.md)에 맞춰 /app에 둔다.
COPY references.md /app/references.md

# whitenoise가 서빙할 정적 파일. DB 접속 없이 수집 가능해야 한다.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

# 상시 프로세스 — 요청이 없어도 죽지 않는다(콜드스타트 없음).
# 저가 머신(256~512MB) 기준 워커 2 + 스레드 4. 딥리포트가 스레드로 도니 gthread가 맞다.
CMD gunicorn config.wsgi \
    --bind 0.0.0.0:${PORT} \
    --workers 2 --threads 4 --worker-class gthread \
    --timeout 120 --access-logfile - --error-logfile -
