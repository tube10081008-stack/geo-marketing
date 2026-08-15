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

퍼블릭 도메인 예제(Amazing Grace SATB)로 전체 파이프라인을 검증합니다.

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
  # role: accompaniment 을 주면 반주 트랙으로 취급되어 모든 믹스에 배경으로 깔립니다.

render:
  lead_program: 52       # 강조 성부 GM 음색
  backing_program: 0     # 배경 성부 GM 음색
  lead_velocity: 112
  backing_velocity: 58
  backing_volume: 46
  count_in_bars: 1       # 카운트인 클릭 마디 수
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

## 실제 악보 투입

1. **악보 입력** — MuseScore 4에서 스코어를 입력합니다. 이 단계가 유일한 병목이며
   곡당 2~4시간입니다. PDF가 있다면 OMR(Audiveris·PhotoScore)로 MusicXML을 뽑아
   교정하는 편이 처음부터 입력하는 것보다 빠릅니다.
2. **MIDI 추출** — `mscore score.mscz -o score.mid`
3. **프로젝트 파일 작성** — 위 YAML에서 `source`와 `parts`만 맞추면 됩니다.
4. **배치 실행** — `python3 -m parttrack.cli build project.yaml`

성부 하나를 다시 뽑을 때는 `--only bass-per_part` 또는 `--only bass`,
영상 없이 음원만 필요하면 `--no-video`를 씁니다.

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
