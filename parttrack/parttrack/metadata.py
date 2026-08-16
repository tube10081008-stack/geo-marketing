"""Generate YouTube titles, descriptions, tags and chapters for each mix.

Search demand in this niche is overwhelmingly problem-shaped — someone has a
performance coming up and needs their own line. Titles therefore lead with the
work and the part, which is what people actually type.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import ProjectConfig
from .mixdown import MixSpec
from .score import Score
from .timing import effective_tempo_map, marker_points, section_spans

# YouTube drops a chapter list whose entries sit closer together than this.
MIN_CHAPTER_GAP = 10.0

VARIANT_LABELS = {
    "per_part": "{part} 파트 강조",
    "part_only": "{part} 파트 단독",
    "full": "전체 합창",
}

VARIANT_BLURBS = {
    "per_part": (
        "{part} 성부를 앞으로 끌어올리고 나머지 성부는 배경으로 낮춘 버전입니다. "
        "화음 속에서 내 파트를 찾는 연습에 사용하세요."
    ),
    "part_only": (
        "{part} 성부만 남긴 버전입니다. 음을 처음 외울 때, 그리고 다른 성부에 "
        "끌려가지 않는지 확인할 때 사용하세요."
    ),
    "full": (
        "모든 성부를 균등하게 합친 전체 버전입니다. 파트 연습을 마친 뒤 전체 화음 "
        "속에서 자기 위치를 확인할 때 사용하세요."
    ),
}

RIGHTS_NOTES = {
    "public-domain": (
        "이 음원은 퍼블릭 도메인 악보를 기반으로 직접 제작한 것으로, 자유롭게 "
        "연습에 사용하실 수 있습니다."
    ),
    "original": (
        "이 음원은 자체 편곡·제작한 오리지널 트랙입니다. 연습 목적의 사용은 자유롭게 "
        "허용됩니다."
    ),
    "licensed": (
        "이 음원은 권리사 협의를 거쳐 제작된 연습용 자료입니다. 무단 재배포는 "
        "삼가 주세요."
    ),
    "unspecified": "",
}

BASE_TAGS = ["파트연습", "합창연습", "성부연습", "뮤지컬연습", "화음연습", "연습음원"]


@dataclass
class MixMetadata:
    slug: str
    title: str
    description: str
    tags: list[str]
    chapters: list[tuple[float, str]] = field(default_factory=list)
    filename_stem: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["chapters"] = [
            {"time": _timestamp(at), "label": label} for at, label in self.chapters
        ]
        return data


def _timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def variant_label(project: ProjectConfig, spec: MixSpec) -> str:
    part_name = (
        project.part(spec.lead_part_id).name if spec.lead_part_id else "전체"
    )
    return VARIANT_LABELS[spec.variant].format(part=part_name)


def build_metadata(
    project: ProjectConfig,
    score: Score,
    spec: MixSpec,
    count_in_s: float = 0.0,
) -> MixMetadata:
    part_name = project.part(spec.lead_part_id).name if spec.lead_part_id else "전체"
    label = variant_label(project, spec)

    work = project.work or project.title
    title_bits = [f"{work} '{project.title}'", label, "연습 영상"]
    if spec.tempo_label:
        title_bits.insert(2, f"({spec.tempo_label})")
    title = " ".join(title_bits)
    if project.channel_name:
        title = f"{title} | {project.channel_name}"

    description = _build_description(project, score, spec, part_name, label, count_in_s)
    tags = _build_tags(project, part_name)
    chapters = _build_chapters(project, score, spec, count_in_s)

    return MixMetadata(
        slug=spec.slug,
        title=title,
        description=description,
        tags=tags,
        chapters=chapters,
        filename_stem=spec.slug,
    )


def _build_description(
    project: ProjectConfig,
    score: Score,
    spec: MixSpec,
    part_name: str,
    label: str,
    count_in_s: float,
) -> str:
    work = project.work or project.title
    lines: list[str] = [
        f"{work} 중 '{project.title}' — {label} 연습 영상입니다.",
        "",
        VARIANT_BLURBS[spec.variant].format(part=part_name),
        "",
        "■ 사용 방법",
        "1. 한 마디 카운트인 클릭에 맞춰 들어오세요.",
        "2. 화면의 피아노롤에서 자기 파트 색을 따라가며 음정을 확인하세요.",
        "3. 익숙해지면 '전체 합창' 버전으로 넘어가 화음 속 균형을 확인하세요.",
    ]

    rubato = [
        span
        for span in section_spans(project, score, spec.tempo_scale)
        if span.rubato or not span.click
    ]
    if rubato:
        names = ", ".join(f"{span.name}({span.from_bar}~{span.to_bar}마디)" for span in rubato)
        lines.append(
            f"※ {names} 구간은 지휘를 따라가는 자유 템포입니다. 클릭 없이 진행되니 "
            "음정과 흐름만 익히고, 실제 박은 연습에서 지휘에 맞추세요."
        )

    lines.extend(["", "■ 정보"])

    info: list[tuple[str, str]] = [("작품", work), ("곡", project.title)]
    if project.composer:
        info.append(("작곡/편곡", project.composer))
    if project.key:
        info.append(("조성", project.key))
    info.append(("성부", part_name))
    if project.reference_parts:
        info.append(
            (
                "가이드",
                ", ".join(p.name or p.id for p in project.reference_parts)
                + " (위치 확인용, 연습 대상 아님)",
            )
        )
    bars = max(0, len(score.bar_ticks()) - 1)
    info.append(("마디 수", f"{bars}마디"))
    if spec.tempo_scale != 1.0:
        info.append(("템포", f"원 템포의 {int(round(spec.tempo_scale * 100))}%"))
    if count_in_s > 0:
        info.append(("카운트인", f"{project.render.count_in_bars}마디"))
    lines.extend(f"· {key}: {value}" for key, value in info)

    note = project.rights_note or RIGHTS_NOTES.get(project.rights, "")
    if note:
        lines.extend(["", "■ 이용 안내", note])

    other_parts = [p.name for p in project.voice_parts if p.name != part_name]
    if other_parts:
        lines.extend(
            [
                "",
                f"다른 성부 영상도 함께 올라와 있습니다: {', '.join(other_parts)}.",
            ]
        )
    return "\n".join(lines)


def _build_tags(project: ProjectConfig, part_name: str) -> list[str]:
    work = project.work or project.title
    tags = [
        work,
        project.title,
        f"{work} {project.title}",
        f"{project.title} {part_name}",
        f"{work} 연습음원",
        part_name,
        *BASE_TAGS,
    ]
    # Preserve order while dropping duplicates and blanks.
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _build_chapters(
    project: ProjectConfig, score: Score, spec: MixSpec, count_in_s: float
) -> list[tuple[float, str]]:
    """Chapters follow the score's own sections, falling back to 8-bar blocks."""
    # Named anchors come from the score's own structure; generic 8-bar entries
    # fill the gaps between them so long sections stay navigable.
    anchors: list[tuple[float, str]] = []
    spans = section_spans(project, score, spec.tempo_scale)
    for index, span in enumerate(spans):
        # The count-in runs straight into the first section, so that section
        # takes the 0:00 slot rather than burning a chapter on two seconds.
        at = 0.0 if index == 0 else span.start_s
        anchors.append((at, f"{span.name} ({span.from_bar}마디~)"))
    for marker in marker_points(project, score, spec.tempo_scale):
        anchors.append((marker.at_s, f"{marker.label} ({marker.bar}마디)"))
    anchors = _thin_chapters(anchors, require_minimum=False)

    if not anchors and count_in_s > 0:
        anchors.append((0.0, "카운트인"))

    scaled = effective_tempo_map(project, score, spec.tempo_scale)
    bar_ticks = score.bar_ticks()
    fillers: list[tuple[float, str]] = []
    for bar_index in range(0, max(len(bar_ticks) - 1, 0), 8):
        at = count_in_s + scaled.tick_to_second(bar_ticks[bar_index])
        if any(abs(at - anchor_at) < MIN_CHAPTER_GAP for anchor_at, _ in anchors):
            continue
        fillers.append((at, f"{bar_index + 1}마디"))

    return _thin_chapters(sorted(anchors + fillers, key=lambda item: item[0]))


def _thin_chapters(
    chapters: list[tuple[float, str]],
    minimum_gap: float = MIN_CHAPTER_GAP,
    require_minimum: bool = True,
) -> list[tuple[float, str]]:
    """YouTube ignores chapter lists whose entries are closer than 10 seconds."""
    kept: list[tuple[float, str]] = []
    for at, label in chapters:
        if kept and at - kept[-1][0] < minimum_gap:
            continue
        kept.append((at, label))
    if not require_minimum:
        return kept
    # A chapter list is only honoured when it starts at zero and has three entries.
    if kept and kept[0][0] > 0:
        kept.insert(0, (0.0, "시작"))
    return kept if len(kept) >= 3 else []


def write_metadata(metadata: MixMetadata, destination: Path) -> Path:
    """Write both a machine-readable JSON file and a paste-ready text file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    readable = destination.with_suffix(".txt")
    chapter_lines = [f"{_timestamp(at)} {label}" for at, label in metadata.chapters]
    readable.write_text(
        "\n".join(
            [
                "=== TITLE ===",
                metadata.title,
                "",
                "=== DESCRIPTION ===",
                metadata.description,
                "",
                "=== CHAPTERS ===",
                *chapter_lines,
                "",
                "=== TAGS ===",
                ", ".join(metadata.tags),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination
