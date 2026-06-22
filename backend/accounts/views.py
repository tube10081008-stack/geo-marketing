"""
계정 인증 — 회원가입 / 로그인 / 내정보. DRF 토큰 인증(계정별 워크스페이스의 기반).

토큰을 받아 Authorization: Token <키> 헤더로 모든 보호 엔드포인트 호출.
"""
from django.contrib.auth import authenticate, get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

User = get_user_model()


def _token_payload(user):
    token, _ = Token.objects.get_or_create(user=user)
    return {"token": token.key, "username": user.username, "email": user.email}


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        email = (request.data.get("email") or "").strip()
        if len(username) < 3 or len(password) < 6:
            return Response(
                {"detail": "아이디는 3자 이상, 비밀번호는 6자 이상이어야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if User.objects.filter(username=username).exists():
            return Response({"detail": "이미 사용 중인 아이디입니다."}, status=status.HTTP_409_CONFLICT)
        user = User.objects.create_user(username=username, password=password, email=email)
        return Response(_token_payload(user), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = authenticate(
            username=(request.data.get("username") or "").strip(),
            password=request.data.get("password") or "",
        )
        if user is None:
            return Response({"detail": "아이디 또는 비밀번호가 올바르지 않습니다."},
                            status=status.HTTP_401_UNAUTHORIZED)
        return Response(_token_payload(user))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response({"username": u.username, "email": u.email})
