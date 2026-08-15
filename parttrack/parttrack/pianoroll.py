"""Scrolling piano-roll video renderer.

The whole score is drawn once into a wide canvas; each frame is then a slice of
that canvas plus a playhead and a highlight pass over the notes currently
sounding. That keeps per-frame work to a memcpy and a handful of rectangles, so
a full batch renders in minutes rather than hours.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .audio import require
from .config import ProjectConfig
from .mixdown import LEVEL_LEAD, LEVEL_MUTED, MixSpec
from .score import Score

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareRoundR.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)

BACKING_DIM = 0.38
ACTIVE_MIX = 0.45
PITCH_PADDING = 2
MIN_NOTE_HEIGHT = 3


def find_font(size: int) -> ImageFont.FreeTypeFont:
    """Resolve a Korean-capable font, falling back to PIL's bitmap default."""
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    # Also sweep the nanum directory in case the packaged filenames differ.
    nanum = Path("/usr/share/fonts/truetype/nanum")
    if nanum.is_dir():
        for path in sorted(nanum.glob("*.ttf")):
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _blend(color: tuple[int, int, int], target: tuple[int, int, int], amount: float):
    return tuple(
        int(round(channel + (target[index] - channel) * amount))
        for index, channel in enumerate(color)
    )


def _dim(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(int(round(channel * factor)) for channel in color)


@dataclass
class _DrawnNote:
    x0: int
    x1: int
    y0: int
    y1: int
    start_s: float
    end_s: float
    color: tuple[int, int, int]
    is_lead: bool


class PianoRollRenderer:
    def __init__(
        self,
        project: ProjectConfig,
        score: Score,
        spec: MixSpec,
        levels: dict[str, str],
        count_in_s: float,
        duration_s: float,
    ):
        self.project = project
        self.score = score
        self.spec = spec
        self.levels = levels
        self.count_in_s = count_in_s
        self.duration_s = duration_s
        self.video = project.video
        self.tempo_map = score.tempo_map.scaled(spec.tempo_scale)

        self._notes: list[_DrawnNote] = []
        self._canvas: np.ndarray | None = None
        self._header: np.ndarray | None = None
        self._bar_chips: list[np.ndarray] = []
        self._bar_times: list[float] = []

    # -- geometry ---------------------------------------------------------
    def _visible_parts(self) -> list[str]:
        return [pid for pid, level in self.levels.items() if level != LEVEL_MUTED]

    def _pitch_bounds(self) -> tuple[int, int]:
        pitches = [
            note.pitch
            for note in self.score.notes
            if self.levels.get(note.part_id, LEVEL_MUTED) != LEVEL_MUTED
        ]
        if not pitches:
            return (60, 72)
        return (min(pitches) - PITCH_PADDING, max(pitches) + PITCH_PADDING)

    def _time_of(self, tick: int) -> float:
        return self.count_in_s + self.tempo_map.tick_to_second(tick)

    # -- canvas -----------------------------------------------------------
    def _build_canvas(self) -> np.ndarray:
        video = self.video
        pps = video.px_per_second
        roll_h = video.roll_height
        canvas_w = int(self.duration_s * pps) + video.width + video.playhead_x + 8

        canvas = np.zeros((roll_h, canvas_w, 3), dtype=np.uint8)
        canvas[:, :] = video.background_rgb

        low, high = self._pitch_bounds()
        span = max(1, high - low + 1)
        lane_h = roll_h / span

        def lane_y(pitch: int) -> tuple[int, int]:
            top = roll_h - (pitch - low + 1) * lane_h
            y0 = int(round(top))
            y1 = int(round(top + lane_h))
            if y1 - y0 < MIN_NOTE_HEIGHT:
                y1 = y0 + MIN_NOTE_HEIGHT
            return max(0, y0), min(roll_h, y1)

        # Octave banding gives the eye a stable vertical reference.
        band = _blend(video.background_rgb, (255, 255, 255), 0.045)
        line = _blend(video.background_rgb, (255, 255, 255), 0.13)
        for pitch in range(low, high + 1):
            if pitch % 12 in (1, 3, 6, 8, 10):  # black keys
                y0, y1 = lane_y(pitch)
                canvas[y0:y1, :] = band
            if pitch % 12 == 0:  # every C
                y0, _ = lane_y(pitch)
                canvas[max(0, y0 - 1) : y0 + 1, :] = line

        # Barlines, so a singer can locate a rehearsal mark by eye.
        barline = _blend(video.background_rgb, (255, 255, 255), 0.10)
        downbeat = _blend(video.background_rgb, (255, 255, 255), 0.20)
        for index, tick in enumerate(self.score.bar_ticks()):
            x = video.playhead_x + int(self._time_of(tick) * pps)
            if 0 <= x < canvas_w:
                canvas[:, x : x + 1] = downbeat if index % 4 == 0 else barline

        # Notes, painted back to front so the lead sits on top.
        drawn: list[_DrawnNote] = []
        ordered = sorted(
            self.score.notes,
            key=lambda note: self.levels.get(note.part_id, LEVEL_MUTED) == LEVEL_LEAD,
        )
        for note in ordered:
            level = self.levels.get(note.part_id, LEVEL_MUTED)
            if level == LEVEL_MUTED:
                continue
            is_lead = level == LEVEL_LEAD
            base = self.project.part(note.part_id).rgb
            color = base if is_lead else _dim(base, BACKING_DIM)

            start_s = self._time_of(note.start_tick)
            end_s = self._time_of(note.end_tick)
            x0 = video.playhead_x + int(start_s * pps)
            x1 = video.playhead_x + int(end_s * pps)
            x1 = max(x1 - 1, x0 + 2)  # 1px gap between repeated notes
            y0, y1 = lane_y(note.pitch)
            if x0 >= canvas_w:
                continue
            x1 = min(x1, canvas_w)

            canvas[y0:y1, x0:x1] = color
            drawn.append(
                _DrawnNote(x0, x1, y0, y1, start_s, end_s, color, is_lead)
            )

        self._notes = drawn
        return canvas

    # -- header -----------------------------------------------------------
    def _build_header(self) -> np.ndarray:
        video = self.video
        image = Image.new(
            "RGB",
            (video.width, video.header_height),
            _blend(video.background_rgb, (255, 255, 255), 0.06),
        )
        draw = ImageDraw.Draw(image)

        title_font = find_font(40)
        sub_font = find_font(21)
        chip_font = find_font(18)

        part_name = (
            self.project.part(self.spec.lead_part_id).name
            if self.spec.lead_part_id
            else "전체"
        )
        variant_word = {
            "per_part": "파트 강조",
            "part_only": "파트 단독",
            "full": "합창",
        }[self.spec.variant]
        headline = f"{part_name} {variant_word}"
        if self.spec.tempo_label:
            headline = f"{headline} · {self.spec.tempo_label}"

        accent = (
            self.project.part(self.spec.lead_part_id).rgb
            if self.spec.lead_part_id
            else (240, 240, 240)
        )
        draw.rectangle([0, 0, 8, video.header_height], fill=accent)
        draw.text((30, 22), headline, font=title_font, fill=(245, 245, 248))

        work = self.project.work or self.project.title
        subtitle = f"{work} — {self.project.title}"
        if self.project.key:
            subtitle = f"{subtitle}  ·  {self.project.key}"
        draw.text((32, 78), subtitle, font=sub_font, fill=(158, 165, 178))

        # Colour legend for the parts present in this mix.
        x = video.width - 30
        for part in reversed(self.project.parts):
            level = self.levels.get(part.id, LEVEL_MUTED)
            if level == LEVEL_MUTED:
                continue
            is_lead = level == LEVEL_LEAD
            label = part.name or part.id
            text_width = int(draw.textlength(label, font=chip_font))
            x -= text_width
            draw.text(
                (x, 26),
                label,
                font=chip_font,
                fill=(238, 238, 240) if is_lead else (130, 136, 148),
            )
            x -= 12
            swatch = part.rgb if is_lead else _dim(part.rgb, BACKING_DIM)
            draw.rectangle([x - 14, 28, x - 2, 40], fill=swatch)
            x -= 32

        return np.array(image, dtype=np.uint8)

    def _build_bar_chips(self) -> None:
        font = find_font(22)
        video = self.video
        self._bar_times = [self._time_of(tick) for tick in self.score.bar_ticks()]
        background = _blend(video.background_rgb, (255, 255, 255), 0.06)
        width = 150
        for index in range(len(self._bar_times)):
            image = Image.new("RGB", (width, 32), background)
            draw = ImageDraw.Draw(image)
            label = f"{index + 1}마디"
            offset = width - int(draw.textlength(label, font=font))
            draw.text((max(0, offset), 2), label, font=font, fill=(198, 204, 216))
            self._bar_chips.append(np.array(image, dtype=np.uint8))

    def _current_bar(self, t: float) -> int:
        index = 0
        for position, start in enumerate(self._bar_times):
            if start <= t + 1e-6:
                index = position
            else:
                break
        return index

    # -- frames -----------------------------------------------------------
    def render(self, audio_path: Path, out_path: Path) -> Path:
        require("ffmpeg")
        video = self.video
        self._canvas = self._build_canvas()
        self._header = self._build_header()
        self._build_bar_chips()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        total_frames = max(1, int(round(self.duration_s * video.fps)))
        header_h = video.header_height
        pps = video.px_per_second

        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{video.width}x{video.height}",
            "-framerate",
            str(video.fps),
            "-i",
            "-",
            "-i",
            str(audio_path),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            video.preset,
            "-crf",
            str(video.crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_path),
        ]

        frame = np.empty((video.height, video.width, 3), dtype=np.uint8)
        playhead_x = video.playhead_x
        playhead_color = (255, 255, 255)

        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        assert process.stdin is not None
        try:
            for index in range(total_frames):
                t = index / video.fps
                x0 = int(t * pps)
                window = self._canvas[:, x0 : x0 + video.width]

                frame[:header_h] = self._header
                frame[header_h:] = video.background_rgb
                frame[header_h : header_h + window.shape[0], : window.shape[1]] = window

                # Light up whatever is sounding right now.
                for note in self._notes:
                    if note.start_s <= t < note.end_s:
                        sx0 = note.x0 - x0
                        sx1 = note.x1 - x0
                        if sx1 <= 0 or sx0 >= video.width:
                            continue
                        sx0 = max(0, sx0)
                        sx1 = min(video.width, sx1)
                        highlight = _blend(note.color, (255, 255, 255), ACTIVE_MIX)
                        frame[
                            header_h + note.y0 : header_h + note.y1, sx0:sx1
                        ] = highlight

                frame[header_h:, playhead_x : playhead_x + 2] = playhead_color

                # Progress bar along the bottom of the header.
                filled = int(video.width * min(1.0, t / max(self.duration_s, 1e-6)))
                frame[header_h - 4 : header_h, :filled] = (
                    self.project.part(self.spec.lead_part_id).rgb
                    if self.spec.lead_part_id
                    else (200, 200, 205)
                )

                bar_index = self._current_bar(t)
                if bar_index < len(self._bar_chips) and t >= self.count_in_s:
                    chip = self._bar_chips[bar_index]
                    chip_x = video.width - 30 - chip.shape[1]
                    frame[84 : 84 + chip.shape[0], chip_x : chip_x + chip.shape[1]] = chip

                process.stdin.write(frame.tobytes())
            process.stdin.close()
        except BrokenPipeError as error:
            stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
            raise RuntimeError(f"ffmpeg closed the pipe early:\n{stderr}") from error

        returncode = process.wait()
        if returncode != 0:
            stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
            raise RuntimeError(f"ffmpeg failed (exit {returncode}):\n{stderr}")
        return out_path
