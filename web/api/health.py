"""
Vercel 서버리스: GET /api/health — 게스트 모드 엔진 상태(라이브/데모) 즉시 확인.

키 값은 절대 노출하지 않고, 설정 여부만 provider 이름으로 알린다.
게스트 모드는 이 Vercel 함수를 쓰므로 여기서 보는 provider가 게스트의 실제 엔진이다.
"""
import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        provider = "gemini" if os.environ.get("GEMINI_API_KEY") else "stub"
        body = json.dumps({"status": "ok", "provider": provider}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
