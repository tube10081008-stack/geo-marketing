"""Build a rehearsal kit from an actual recording.

Separating alto from tenor inside a finished mix is not something current tools
can do — harmony sung by similar voices in the same range is not separable, so
there is no honest path from a stereo master to an isolated inner part. What a
recording *is* good for is everything else singers do with one: working a
section at reduced tempo, looping the entrance they keep missing, and running
the number against a vocal-reduced backing.

This module produces that kit. Bar numbers from the score are mapped onto the
recording's clock with a handful of hand-placed anchors, so the sections already
declared in the project file address real audio without being re-entered.
"""

from __future__ import annotations

import bisect
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import require
from .config import Anchor, ProjectConfig, RecordingConfig, SectionConfig

__all__ = [
    "Anchor",
    "RecordingConfig",
    "bar_to_time",
    "build_kit",
    "local_seconds_per_bar",
    "reduce_center",
    "section_slug",
    "stretch",
]

CLICK_FREQ = 1400.0
CLICK_ACCENT_FREQ = 2100.0
CLICK_LEN = 0.035
CLICK_SR = 44100

# Below this, a stereo master's bass is effectively mono; cancelling it there
# would gut the track without removing any more voice.
CENTER_CANCEL_LOW = 180.0
CENTER_CANCEL_HIGH = 9000.0


def bar_to_time(config: RecordingConfig, bar: float) -> float:
    """Piecewise-linear bar -> seconds, extrapolating past the outer anchors.

    A handful of anchors beats a global tempo because theatre recordings do not
    hold one: a colla voce verse and the section after it sit on different
    clocks, and interpolating between anchors follows that.
    """
    anchors = config.anchors
    bars = [anchor.bar for anchor in anchors]

    if bar <= bars[0]:
        first, second = anchors[0], anchors[1]
    elif bar >= bars[-1]:
        first, second = anchors[-2], anchors[-1]
    else:
        index = bisect.bisect_right(bars, bar) - 1
        first, second = anchors[index], anchors[index + 1]

    span_bars = second.bar - first.bar
    if span_bars == 0:
        return first.at
    ratio = (bar - first.bar) / span_bars
    return first.at + ratio * (second.at - first.at)


def local_seconds_per_bar(config: RecordingConfig, bar: int) -> float:
    return max(1e-3, bar_to_time(config, bar + 1) - bar_to_time(config, bar))


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError(
            f"command failed ({command[0]}, exit {result.returncode}):\n"
            + "\n".join(tail)
        )


def make_click(
    beats: int, seconds_per_beat: float, destination: Path, sample_rate: int = CLICK_SR
) -> Path:
    """Synthesise a count-in click at the recording's own local tempo."""
    total = max(1, int(round(beats * seconds_per_beat * sample_rate)))
    buffer = np.zeros((total, 2), dtype=np.float32)
    click_samples = int(CLICK_LEN * sample_rate)
    envelope = np.exp(-np.linspace(0, 9, click_samples)).astype(np.float32)

    for beat in range(beats):
        start = int(round(beat * seconds_per_beat * sample_rate))
        end = min(total, start + click_samples)
        if end <= start:
            continue
        frequency = CLICK_ACCENT_FREQ if beat == 0 else CLICK_FREQ
        phase = np.arange(end - start) / sample_rate
        tone = np.sin(2 * np.pi * frequency * phase).astype(np.float32)
        tone *= envelope[: end - start] * (0.55 if beat == 0 else 0.38)
        buffer[start:end, 0] += tone
        buffer[start:end, 1] += tone

    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(destination, buffer, sample_rate)
    return destination


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    import wave

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(pcm.shape[1] if pcm.ndim > 1 else 1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def slice_audio(
    source: Path, start: float, end: float, destination: Path, fade: float = 0.04
) -> Path:
    """Cut [start, end) with short fades so section edges do not click."""
    require("ffmpeg")
    duration = max(0.05, end - start)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fade = min(fade, duration / 4)
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-af",
            f"afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}",
            str(destination),
        ]
    )
    return destination


def stretch(source: Path, destination: Path, factor: float) -> Path:
    """Slow down without changing pitch. rubberband when built in, atempo else."""
    require("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if factor == 1.0:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), str(destination)])
        return destination

    if _has_filter("rubberband"):
        chain = f"rubberband=tempo={factor}:pitchq=quality:smoothing=on"
    else:
        # atempo only accepts 0.5..2.0 per instance, so deep slowdowns chain.
        chain = ",".join(f"atempo={value}" for value in _atempo_chain(factor))
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            chain,
            str(destination),
        ]
    )
    return destination


def _atempo_chain(factor: float) -> list[float]:
    values: list[float] = []
    remaining = factor
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    values.append(round(remaining, 6))
    return values


_FILTER_CACHE: dict[str, bool] = {}


def _has_filter(name: str) -> bool:
    if name not in _FILTER_CACHE:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
        )
        _FILTER_CACHE[name] = f" {name} " in result.stdout
    return _FILTER_CACHE[name]


def loop_with_click(
    section: Path,
    click: Path,
    destination: Path,
    repeats: int,
    gap: float,
) -> Path:
    """Count-in, section, gap, count-in, section ... for drilling one passage.

    Every piece is decoded to one uniform PCM format first: the concat demuxer
    copies stream parameters from the first input and silently truncates when a
    later file disagrees, which quietly turns a three-pass loop into one pass.
    """
    require("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp: list[Path] = []

    def _as_pcm(path: Path, tag: str) -> Path:
        out = destination.parent / f"{destination.stem}.{tag}.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-ar",
                str(CLICK_SR),
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(out),
            ]
        )
        temp.append(out)
        return out

    click_pcm = _as_pcm(click, "click")
    section_pcm = _as_pcm(section, "section")

    silence = destination.parent / f"{destination.stem}.gap.wav"
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={CLICK_SR}:cl=stereo",
            "-t",
            f"{max(0.1, gap):.3f}",
            "-c:a",
            "pcm_s16le",
            str(silence),
        ]
    )
    temp.append(silence)

    order: list[Path] = []
    for index in range(repeats):
        order.extend([click_pcm, section_pcm])
        if index < repeats - 1:
            order.append(silence)

    listing = destination.parent / f"{destination.stem}.concat.txt"
    listing.write_text(
        "\n".join(f"file '{path.resolve()}'" for path in order) + "\n", encoding="utf-8"
    )
    temp.append(listing)

    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-ar",
                str(CLICK_SR),
                "-ac",
                "2",
                str(destination),
            ]
        )
    finally:
        for path in temp:
            path.unlink(missing_ok=True)
    return destination


def reduce_center(source: Path, destination: Path) -> Path:
    """Band-limited centre cancellation: a rough vocal-reduced backing.

    L-R removes anything panned dead centre, which on most mixes is the lead
    vocal. Bass and air are taken from the untouched signal so the track keeps
    its bottom end. Anything doubled or reverb-spread survives, so treat the
    result as a rehearsal aid, not a released instrumental.
    """
    require("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    chain = (
        "[0:a]asplit=3[low][mid][high];"
        f"[low]lowpass=f={CENTER_CANCEL_LOW}[l];"
        f"[high]highpass=f={CENTER_CANCEL_HIGH}[h];"
        "[mid]pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c0-0.5*c1,"
        f"highpass=f={CENTER_CANCEL_LOW},lowpass=f={CENTER_CANCEL_HIGH}[m];"
        "[l][m][h]amix=inputs=3:normalize=0[out]"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            chain,
            "-map",
            "[out]",
            str(destination),
        ]
    )
    return destination


def emphasise_center(source: Path, destination: Path) -> Path:
    """Mono sum with the sides pulled down — pushes the lead forward. Rough."""
    require("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
            "stereotools=mlev=1.0:slev=0.25",
            str(destination),
        ]
    )
    return destination


def overlay_bed(
    part_audio: Path,
    recording: Path,
    destination: Path,
    offset: float,
    bed_gain_db: float,
) -> Path:
    """Lay the synthesised part over the actual recording.

    This is the format the practice videos in this niche actually use: the real
    performance sits underneath at a low level so the singer hears the number as
    it goes, and a clean synth rendering of their own line sits on top so the
    pitches are never in doubt. Nothing is separated — the part is added.

    `offset` is where bar 1 of the score falls in the output, so a positive
    value delays the recording and a negative one trims into it.
    """
    require("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)

    inputs = ["-i", str(part_audio)]
    if offset < 0:
        inputs += ["-ss", f"{-offset:.3f}", "-i", str(recording)]
        bed_chain = f"[1:a]volume={bed_gain_db}dB[bed]"
    else:
        milliseconds = int(round(offset * 1000))
        inputs += ["-i", str(recording)]
        bed_chain = (
            f"[1:a]adelay={milliseconds}|{milliseconds},volume={bed_gain_db}dB[bed]"
        )

    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            f"{bed_chain};[0:a][bed]amix=inputs=2:duration=longest:normalize=0[out]",
            "-map",
            "[out]",
            str(destination),
        ]
    )
    return destination


def section_slug(section: SectionConfig) -> str:
    """ASCII filename stem — section names are usually Korean."""
    ascii_name = "".join(
        char if char.isalnum() else "-" for char in section.name if char.isascii()
    ).strip("-")
    if ascii_name:
        return f"{ascii_name.lower()}-{section.from_bar}-{section.to_bar}"
    return f"bars-{section.from_bar}-{section.to_bar}"


def build_kit(project: ProjectConfig, out_dir: Path) -> list[dict]:
    """Produce the full rehearsal kit for a project's recording."""
    recording = project.recording
    if recording is None:
        raise ValueError("project has no `recording:` block")

    source = recording.source
    if not source.is_absolute():
        source = project.root / source
    if not source.exists():
        raise FileNotFoundError(f"recording not found: {source}")

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = recording.audio_format
    records: list[dict] = []

    if recording.vocal_reduce:
        path = reduce_center(source, out_dir / f"backing-vocal-reduced.{suffix}")
        records.append(
            {"kind": "backing", "file": path.name, "note": "센터 감쇄 MR (근사)"}
        )
    if recording.vocal_focus:
        path = emphasise_center(source, out_dir / f"vocal-focus.{suffix}")
        records.append(
            {"kind": "vocal-focus", "file": path.name, "note": "보컬 강조 (근사)"}
        )

    total_bars = max(
        (section.to_bar for section in project.sections), default=recording.anchors[-1].bar
    )
    for label, scale in recording.tempo_variants.items():
        stem = f"full-{label}" if label else "full"
        path = stretch(source, out_dir / f"{stem}.{suffix}", scale)
        records.append(
            {"kind": "full", "file": path.name, "tempo": scale, "label": label}
        )

    for section in project.sections:
        slug = section_slug(section)
        start = bar_to_time(recording, section.from_bar) - recording.lead_in
        end = bar_to_time(recording, min(section.to_bar + 1, total_bars + 1))
        raw = slice_audio(source, max(0.0, start), end, out_dir / f"{slug}.{suffix}")
        records.append(
            {
                "kind": "section",
                "file": raw.name,
                "section": section.name,
                "bars": [section.from_bar, section.to_bar],
                "start_s": round(max(0.0, start), 2),
                "end_s": round(end, 2),
            }
        )

        seconds_per_beat = (
            local_seconds_per_bar(recording, section.from_bar) / recording.beats_per_bar
        )
        click = make_click(
            recording.count_in_beats,
            seconds_per_beat,
            out_dir / f"{slug}.click.wav",
        )

        for label, scale in recording.tempo_variants.items():
            slow_name = f"{slug}-{label}" if label else f"{slug}-slow"
            slow = stretch(raw, out_dir / f"{slow_name}.{suffix}", scale)
            slow_click = make_click(
                recording.count_in_beats,
                seconds_per_beat / scale,
                out_dir / f"{slow_name}.click.wav",
            )
            looped = loop_with_click(
                slow,
                slow_click,
                out_dir / f"{slow_name}-loop.{suffix}",
                recording.loop_repeats,
                recording.loop_gap,
            )
            records.append(
                {
                    "kind": "section-loop",
                    "file": looped.name,
                    "section": section.name,
                    "tempo": scale,
                    "repeats": recording.loop_repeats,
                }
            )
            slow_click.unlink(missing_ok=True)

        looped = loop_with_click(
            raw,
            click,
            out_dir / f"{slug}-loop.{suffix}",
            recording.loop_repeats,
            recording.loop_gap,
        )
        records.append(
            {
                "kind": "section-loop",
                "file": looped.name,
                "section": section.name,
                "tempo": 1.0,
                "repeats": recording.loop_repeats,
            }
        )
        click.unlink(missing_ok=True)

    return records
