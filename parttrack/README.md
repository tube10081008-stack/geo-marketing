# parttrack

성부별 연습 음원·영상 배치 생성기.

악보 하나를 넣으면 **파트별 연습 음원 + 스크롤 피아노롤 영상 + 유튜브 메타데이터**를
한 번에 생성합니다. 4성부 곡이면 명령 한 줄로 9개 영상이 나옵니다.

## 설계 원칙: 악보가 유일한 진실

연습 음원의 유일한 요구사항은 **악보와 음이 정확히 같을 것**입니다. 단원이 이 음원으로
외운 음이 음악감독이 잡아준 화성과 다르면 연습을 망칩니다.

그래서 이 파이프라인은 전 구간이 결정론적입니다. 입력 MIDI의 음표를 읽어 그대로
재생하며, 어떤 단계에서도 음을 생성하거나 재해석하지 않습니다. 같은 입력은 항상 같은
출력을 냅니다.

생성형 음악 모델(Suno 등)은 이 자리에 쓸 수 없습니다. 정해진 악보를 재생하는 도구가
아니라 없던 음악을 만드는 도구이기 때문입니다. Suno Studio의 MIDI 기능도 임포트한
MIDI를 **생성 프롬프트로** 사용하는 것이고, 스템→MIDI 변환은 공식 문서상 "러프 스케치"로
안내됩니다. 오리지널·퍼블릭도메인 부가 콘텐츠에는 유용하지만, 연습 음원 본체에는
부적합합니다.

## 설치

```bash
pip install -r requirements.txt
apt-get install -y ffmpeg fluidsynth fluid-soundfont-gm   # Debian/Ubuntu
# macOS: brew install ffmpeg fluid-synth

python3 -m parttrack.cli doctor    # 의존성 확인
```

한글 자막·헤더를 쓰려면 한글 폰트가 필요합니다 (`apt-get install fonts-nanum`).

## 빠른 시작

퍼블릭 도메인 예제 두 개가 들어 있습니다. 둘 다 권리 부담 없이 돌려볼 수 있습니다.

| 예제 | 형태 | 검증하는 것 |
|---|---|---|
| `examples/amazing_grace` | 4성부 찬송가 | 기본 SATB 파트 분리 |
| `examples/ballad_form` | 솔로 + 백보컬 발라드 | 섹션·마커·가이드 성부 |

```bash
cd parttrack
python3 examples/amazing_grace/build_source.py          # 데모 악보 생성
python3 -m parttrack.cli inspect examples/amazing_grace/project.yaml
python3 -m parttrack.cli build   examples/amazing_grace/project.yaml
```

결과:

```
examples/amazing_grace/build/
├── audio/      soprano-per_part.mp3, bass-part_only.mp3, ...
├── video/      soprano-per_part.mp4, ...          (1280x720 / 30fps)
├── midi/       실제로 렌더된 믹스 MIDI (검증·재현용)
├── metadata/   *.json (기계용) + *.txt (붙여넣기용 제목/설명/태그/챕터)
└── index.json  전체 산출물 목록
```

## 산출물 3종

| variant | 내용 | 용도 |
|---|---|---|
| `per_part` | 해당 성부를 강조, 나머지는 배경 | 화음 속에서 내 파트 찾기 |
| `part_only` | 해당 성부만 (반주는 유지) | 음을 처음 외울 때 |
| `full` | 전 성부 균등 | 전체 화음 확인 |

강조 성부는 지속음 음색(기본 GM 52 Choir Aahs)으로 중앙에, 나머지 성부는 피아노로
볼륨을 낮추고 좌우로 벌려 배치합니다. 긴 음의 음정을 귀로 잡기 쉽고, 자기 선율이
텍스처에서 분리돼 들립니다.

## 프로젝트 파일

```yaml
title: "Amazing Grace"          # 곡명
work: "찬송가 / 합창 연습"        # 작품명 (제목·태그 생성에 사용)
composer: "..."
key: "G Major"
rights: public-domain           # public-domain | original | licensed | unspecified
source: amazing_grace.mid       # 악보 소스 (프로젝트 파일 기준 상대경로)
out_dir: build

parts:
  - { id: soprano, name: "소프라노", track: 1 }
  - { id: alto,    name: "알토",     track: 2 }
  - { id: tenor,   name: "테너",     track: 3 }
  - { id: bass,    name: "베이스",   track: 4 }

sections:                # 선택 사항 — 마디 구간별 템포·클릭 제어
  - { name: "1절 (Colla Voce)", from_bar: 1,  to_bar: 8,  rubato: true }
  - { name: "후렴",             from_bar: 9,  to_bar: 14, click: true }
  - { name: "엔딩 (Rall.)",     from_bar: 15, to_bar: 16, tempo_scale: 0.82, click: false }

markers:                 # 선택 사항 — 피아노롤에 세로선 + 라벨
  - { bar: 13, label: "전조 +1", color: "#ffd43b" }

render:
  lead_program: 52       # 강조 성부 GM 음색
  backing_program: 0     # 배경 성부 GM 음색
  reference_program: 73  # 가이드 성부 GM 음색
  lead_velocity: 112
  backing_velocity: 58
  backing_volume: 46
  count_in_bars: 1       # 카운트인 클릭 마디 수
  click_through: false   # 전 구간 클릭. 섹션이 개별로 덮어씁니다
  pan_spread: 26

video:
  width: 1280
  height: 720
  fps: 30
  px_per_second: 110     # 스크롤 속도
  enabled: true

outputs:
  variants: [per_part, part_only, full]
  tempo_variants:        # 라벨 -> 배속. 느린 연습본을 같이 뽑을 때 사용
    "": 1.0
    "느린템포": 0.85
  audio_format: mp3
```

`track`을 생략하면 소스에서 음표가 있는 트랙 순서대로 자동 배정됩니다.

### 성부 역할 (`role`)

| role | 산출물 생성 | 믹스에서의 위치 |
|---|---|---|
| `voice` (기본) | ✅ 성부별 영상 생성 | 강조 또는 배경 |
| `reference` | ❌ | **항상 들림** — 솔로 멜로디·지휘 가이드. 위치 확인용이지 연습 대상이 아님 |
| `accompaniment` | ❌ | 항상 배경 — 피아노 리덕션, 밴드 트랙 |

뮤지컬 넘버는 대개 솔로 위에 백보컬이 얹히는 구조입니다. 솔로를 `reference`로 두면
백보컬 단원이 자기 진입 지점을 들으면서 연습할 수 있고, 솔로용 영상은 만들지 않습니다.

### 섹션과 마커

실제 극장 악보는 메트로놈처럼 흐르지 않습니다. Colla voce 구간은 지휘가 가수를 따라가고
엔딩은 rall.합니다. 섹션을 선언하면:

- `rubato: true` — 클릭이 자동으로 꺼집니다 (지휘를 따라가는 구간에 클릭을 깔면 방해만 됩니다)
- `click: true / false` — 구간별 클릭을 명시적으로 켜고 끕니다
- `tempo_scale` — 해당 구간만 느리게/빠르게 (rall., 느린 연습 구간)
- 영상 상단에 구간 이름이 띠로 표시되고, 유튜브 챕터가 마디 단위가 아니라 **구간 이름**으로 생성됩니다

`markers`는 전조·성부 진입처럼 눈에 띄어야 하는 지점에 세로선과 라벨을 그립니다.

## 실제 악보 투입

1. **악보 입력** — MuseScore 4에서 스코어를 입력합니다. 이 단계가 유일한 병목이며
   곡당 2~4시간입니다. PDF가 있다면 OMR(Audiveris·PhotoScore)로 MusicXML을 뽑아
   교정하는 편이 처음부터 입력하는 것보다 빠릅니다.
2. **MIDI 추출** — `mscore score.mscz -o score.mid`
3. **프로젝트 파일 작성** — 위 YAML에서 `source`와 `parts`만 맞추면 됩니다.
4. **배치 실행** — `python3 -m parttrack.cli build project.yaml`

성부 하나를 다시 뽑을 때는 `--only bass-per_part` 또는 `--only bass`,
영상 없이 음원만 필요하면 `--no-video`를 씁니다.

## 실제 녹음으로 연습 키트 만들기 (`rehearse`)

악보가 아니라 **실제 음원**이 있을 때 쓰는 경로입니다.

### 먼저 알아야 할 한계

**완성된 믹스에서 알토와 테너를 분리하는 것은 불가능합니다.** 같은 음역대를 비슷한
음색으로 부른 화음은 현재 어떤 도구로도 분리되지 않습니다. Demucs 같은 분리 모델도
"보컬"을 **하나의 스템**으로 줄 뿐, 내성부를 따로 꺼내주지 않습니다. 스테레오 마스터에서
알토 라인만 뽑는 정직한 경로는 없습니다.

대신 실제 녹음이 잘하는 일 — 단원들이 실제로 하는 연습 — 을 자동화합니다:

| 산출물 | 내용 |
|---|---|
| 구간 컷 | 마디 구간별로 잘라낸 클립 |
| 감속본 | 피치 유지 타임스트레치 (`rubberband`, 없으면 `atempo`) |
| 구간 루프 | 카운트인 → 구간 → 간격 → 반복. 못 들어가는 진입부 반복 연습용 |
| MR | 센터 채널 감쇄로 리드 보컬을 줄인 반주 (근사) |

### 마디 ↔ 시간 앵커

전체를 한 템포로 가정하지 않고, **손으로 찍은 앵커 몇 개**로 마디를 실제 시간에
매핑합니다. 콜라보체 구간과 그 다음 구간은 서로 다른 시계 위에 있기 때문입니다.

```yaml
recording:
  source: take.mp3
  beats_per_bar: 2          # 6/8이면 점4분음표 2박
  count_in_beats: 4
  loop_repeats: 3
  loop_gap: 1.5
  vocal_reduce: true
  tempo_variants: { "75퍼센트": 0.75 }
  anchors:
    - { bar: 1,  at: 0.0 }
    - { bar: 25, at: 40.0 }
    - { bar: 49, at: 76.0 }
```

`sections`는 악보 경로와 그대로 공유됩니다 — 한 번 선언하면 영상과 실음원 양쪽에서 씁니다.

```bash
python3 -m parttrack.cli rehearse project.yaml
```

앵커를 찍을 때는 음원 분석이 도움이 됩니다. 조성 전환 지점(전조), RMS 레벨 점프(밴드
진입), 레벨 하강(rall.)이 대개 구조 경계와 일치합니다.

### MR 품질에 대한 정직한 기대치

센터 감쇄는 모델이 아니라 위상 연산입니다(L−R). 실측 감쇄량 예시:

| 대역 / 구간 | 감쇄 |
|---|---|
| 보컬 대역 200–4k (전체) | −5.6 dB |
| 저역 <180 (보존) | −1.0 dB |
| 솔로 중심 구간 | −8.6 ~ −9.1 dB |
| 앙상블 구간 | −5.5 ~ −5.9 dB |

리드는 확실히 줄지만 **사라지지는 않습니다.** 더블링·리버브로 퍼진 성분은 남습니다.
연습 보조용으로는 충분하고, 배포용 MR로는 부족합니다.

## 권리 주의

`rights` 값은 설명란 문구를 바꿀 뿐, 권리를 만들어 주지 않습니다.

라이선스 뮤지컬·현행 저작권 악보의 연습 음원을 공개 배포하는 것은 원칙적으로
출판사·권리사의 허락이 필요한 복제·2차적저작물 이용입니다. 유튜브 Content ID가
붙으면 영상은 남더라도 광고 수익은 권리자에게 귀속될 수 있습니다.

퍼블릭 도메인 곡과 자체 편곡으로 파이프라인을 검증한 뒤, 라이선스 작품은 권리 정리를
마치고 투입하는 순서를 권합니다.

## 테스트

```bash
python3 -m pytest tests/ -q          # 외부 바이너리 없이 도는 단위 테스트
```
