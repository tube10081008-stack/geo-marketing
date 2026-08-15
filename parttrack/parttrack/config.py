"""Project configuration model for parttrack.

A project is described by a single YAML file that points at a score source
(MIDI) and declares which tracks correspond to which vocal parts. Everything
else has a sensible default so a minimal project file is only a few lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Colour defaults keyed by part id, chosen to stay distinguishable against the
# dark piano-roll background and to survive YouTube's compression.
DEFAULT_COLORS = {
    "soprano": "#ff6b6b",
    "alto": "#ffa94d",
    "tenor": "#4dabf7",
    "bass": "#51cf66",
    "piano": "#adb5bd",
    "accompaniment": "#adb5bd",
}

FALLBACK_COLOR = "#868e96"

# General MIDI programs. A sustained, voice-like timbre reads far better than a
# piano for learning long choral notes, so the lead part defaults to Choir Aahs
# while the supporting parts sit under it on a piano.
DEFAULT_LEAD_PROGRAM = 52  # Choir Aahs
DEFAULT_BACKING_PROGRAM = 0  # Acoustic Grand Piano

VALID_VARIANTS = ("per_part", "part_only", "full")
VALID_ROLES = ("voice", "accompaniment")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"invalid colour: {value!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


@dataclass
class PartConfig:
    """One singable part, bound to a track in the source MIDI file."""

    id: str
    name: str
    track: int | None = None
    color: str = ""
    role: str = "voice"

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(
                f"part {self.id!r}: role must be one of {VALID_ROLES}, got {self.role!r}"
            )
        if not self.color:
            self.color = DEFAULT_COLORS.get(self.id, FALLBACK_COLOR)

    @property
    def rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.color)

    @property
    def is_voice(self) -> bool:
        return self.role == "voice"


@dataclass
class RenderConfig:
    """How a mix is voiced and balanced."""

    lead_program: int = DEFAULT_LEAD_PROGRAM
    backing_program: int = DEFAULT_BACKING_PROGRAM
    lead_velocity: int = 112
    backing_velocity: int = 58
    lead_volume: int = 127
    backing_volume: int = 46
    count_in_bars: int = 1
    click_note: int = 76  # GM "Hi Wood Block"
    click_accent_note: int = 77
    click_velocity: int = 92
    # Supporting parts are fanned out across the stereo field so a singer can
    # pick their own line out of the texture; the lead stays centred.
    pan_spread: int = 26
    sample_rate: int = 44100
    gain: float = 0.7

    def __post_init__(self) -> None:
        if not 0 <= self.lead_program <= 127:
            raise ValueError("lead_program must be 0..127")
        if not 0 <= self.backing_program <= 127:
            raise ValueError("backing_program must be 0..127")
        if self.count_in_bars < 0:
            raise ValueError("count_in_bars must be >= 0")


@dataclass
class VideoConfig:
    """Piano-roll video geometry."""

    width: int = 1280
    height: int = 720
    fps: int = 30
    px_per_second: float = 110.0
    header_height: int = 132
    playhead_ratio: float = 0.32
    background: str = "#12141a"
    enabled: bool = True
    crf: int = 20
    preset: str = "veryfast"

    @property
    def playhead_x(self) -> int:
        return int(self.width * self.playhead_ratio)

    @property
    def roll_height(self) -> int:
        return self.height - self.header_height

    @property
    def background_rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.background)


@dataclass
class OutputConfig:
    """Which deliverables the batch produces."""

    variants: list[str] = field(default_factory=lambda: ["per_part", "full"])
    # label -> tempo multiplier. 1.0 is the written tempo; 0.85 is a slow
    # practice pass. An empty label keeps the filename clean.
    tempo_variants: dict[str, float] = field(default_factory=lambda: {"": 1.0})
    audio_format: str = "mp3"
    keep_wav: bool = False

    def __post_init__(self) -> None:
        for variant in self.variants:
            if variant not in VALID_VARIANTS:
                raise ValueError(
                    f"unknown variant {variant!r}; expected one of {VALID_VARIANTS}"
                )
        if not self.tempo_variants:
            raise ValueError("tempo_variants must not be empty")
        for label, scale in self.tempo_variants.items():
            if scale <= 0:
                raise ValueError(f"tempo scale for {label!r} must be > 0")


@dataclass
class ProjectConfig:
    """Everything needed to turn one score into a batch of practice videos."""

    title: str
    source: Path
    work: str = ""
    composer: str = ""
    key: str = ""
    rights: str = "unspecified"
    rights_note: str = ""
    channel_name: str = ""
    parts: list[PartConfig] = field(default_factory=list)
    render: RenderConfig = field(default_factory=RenderConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    outputs: OutputConfig = field(default_factory=OutputConfig)
    out_dir: Path = Path("build")
    root: Path = Path(".")

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("project must declare at least one part")
        seen: set[str] = set()
        for part in self.parts:
            if part.id in seen:
                raise ValueError(f"duplicate part id: {part.id}")
            seen.add(part.id)

    @property
    def voice_parts(self) -> list[PartConfig]:
        return [p for p in self.parts if p.is_voice]

    @property
    def accompaniment_parts(self) -> list[PartConfig]:
        return [p for p in self.parts if not p.is_voice]

    def part(self, part_id: str) -> PartConfig:
        for candidate in self.parts:
            if candidate.id == part_id:
                return candidate
        raise KeyError(part_id)

    @property
    def source_path(self) -> Path:
        return self.source if self.source.is_absolute() else self.root / self.source

    @property
    def output_path(self) -> Path:
        return self.out_dir if self.out_dir.is_absolute() else self.root / self.out_dir

    @classmethod
    def load(cls, path: str | Path) -> "ProjectConfig":
        config_path = Path(path).resolve()
        with config_path.open("r", encoding="utf-8") as handle:
            raw: dict[str, Any] = yaml.safe_load(handle) or {}
        return cls.from_dict(raw, root=config_path.parent)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], root: Path = Path(".")) -> "ProjectConfig":
        missing = [key for key in ("title", "source", "parts") if key not in raw]
        if missing:
            raise ValueError(f"project file is missing required keys: {missing}")

        parts = [PartConfig(**part) for part in raw["parts"]]
        return cls(
            title=raw["title"],
            source=Path(raw["source"]),
            work=raw.get("work", ""),
            composer=raw.get("composer", ""),
            key=raw.get("key", ""),
            rights=raw.get("rights", "unspecified"),
            rights_note=raw.get("rights_note", ""),
            channel_name=raw.get("channel_name", ""),
            parts=parts,
            render=RenderConfig(**raw.get("render", {})),
            video=VideoConfig(**raw.get("video", {})),
            outputs=OutputConfig(**raw.get("outputs", {})),
            out_dir=Path(raw.get("out_dir", "build")),
            root=root,
        )
