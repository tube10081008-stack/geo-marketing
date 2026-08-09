#!/usr/bin/env python3
"""단지별 식별코드가 박힌 A5 양면 전단을 생성한다.

식별장치 3중 구조:
  1. 코드 배지     — 전화 문의 시 발화용 (할인 유인으로 발화율 확보)
  2. QR            — SMSTO 스킴. 스캔하면 코드가 본문에 박힌 문자가 자동 작성된다
  3. 뒷면 재인쇄   — 앞면이 와이퍼/오염으로 가려져도 코드가 살아남는다
"""
import io
import json
import segno

PHONE = "01059314144"
REGION = "WJ"          # 운정
BLOCKS = ["A", "B", "C"]   # 단지 (플레이북 §7.3 = 단지 3곳 비교실험)
ROUNDS = ["1", "2", "3"]   # 배포 회차 (반복 노출 효과 측정용)


def qr_svg(code: str) -> str:
    """스캔 시 코드가 본문에 자동 입력되는 문자 작성 QR."""
    payload = f"SMSTO:{PHONE}:[{code}] 세차 문의합니다"
    buf = io.BytesIO()
    segno.make(payload, error="m").save(
        buf, kind="svg", xmldecl=False, svgns=True,
        omitsize=True, unit="", scale=1, border=2, dark="#16365f",
    )
    return buf.getvalue().decode("utf-8")


qrs = {f"{REGION}-{b}{r}": qr_svg(f"{REGION}-{b}{r}")
       for b in BLOCKS for r in ROUNDS}

with open("flyer_template.html", encoding="utf-8") as f:
    html = f.read()

html = html.replace("__QR_DATA__", json.dumps(qrs, ensure_ascii=False))

with open("flyer.html", "w", encoding="utf-8") as f:
    f.write(html)

sample = segno.make(f"SMSTO:{PHONE}:[{REGION}-A1] 세차 문의합니다", error="m")
print(f"생성 완료: {len(qrs)}개 코드 / QR 버전 {sample.version} "
      f"({sample.symbol_size(scale=1, border=0)[0]}모듈) / 오류정정 M(15%)")
