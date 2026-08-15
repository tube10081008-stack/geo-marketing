"""Render mix MIDI to audio with FluidSynth, then encode with ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Distribution-provided General MIDI banks, best first. Override with
# PARTTRACK_SOUNDFONT to use a dedicated choral or orchestral bank.
SOUNDFONT_CANDIDATES = (
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
    "/usr/share/soundfonts/default.sf2",
    "/usr/share/sounds/sf3/default-GM.sf3",
    "/Library/Audio/Sounds/Banks/FluidR3_GM.sf2",
)

# Reverb and release tails outlast the final note-off; without this the last
# chord of every track ends abruptly.
TAIL_SECONDS = 1.8


class ToolMissing(RuntimeError):
    """Raised when an external binary the pipeline depends on is unavailable."""


def find_soundfont(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_value = os.environ.get("PARTTRACK_SOUNDFONT")
    if env_value:
        candidates.append(env_value)
    candidates.extend(SOUNDFONT_CANDIDATES)

    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    raise ToolMissing(
        "no SoundFont found. Install one (e.g. `apt-get install fluid-soundfont-gm`) "
        "or set PARTTRACK_SOUNDFONT to a .sf2 file."
    )


def require(binary: str) -> str:
    resolved = shutil.which(binary)
    if resolved is None:
        raise ToolMissing(
            f"`{binary}` is not on PATH. Install it before running the audio stage."
        )
    return resolved


def check_tools() -> dict[str, str]:
    """Report the availability of every external dependency."""
    report: dict[str, str] = {}
    for binary in ("fluidsynth", "ffmpeg"):
        resolved = shutil.which(binary)
        report[binary] = resolved or "MISSING"
    try:
        report["soundfont"] = str(find_soundfont())
    except ToolMissing as error:
        report["soundfont"] = f"MISSING ({error})"
    return report


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError(
            f"command failed ({command[0]}, exit {result.returncode}):\n"
            + "\n".join(tail)
        )


def render_wav(
    midi_path: Path,
    wav_path: Path,
    sample_rate: int = 44100,
    gain: float = 0.7,
    soundfont: Path | None = None,
) -> Path:
    """Synthesise ``midi_path`` to a padded WAV file."""
    require("fluidsynth")
    bank = soundfont or find_soundfont()
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    raw = wav_path.with_suffix(".raw.wav")
    _run(
        [
            "fluidsynth",
            "-ni",
            "-g",
            f"{gain}",
            "-r",
            str(sample_rate),
            "-F",
            str(raw),
            str(bank),
            str(midi_path),
        ]
    )
    if not raw.exists() or raw.stat().st_size == 0:
        raise RuntimeError(f"fluidsynth produced no audio for {midi_path.name}")

    require("ffmpeg")
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-af",
            f"apad=pad_dur={TAIL_SECONDS}",
            str(wav_path),
        ]
    )
    raw.unlink(missing_ok=True)
    return wav_path


def encode(wav_path: Path, out_path: Path, audio_format: str = "mp3") -> Path:
    """Compress the rendered WAV to the distribution format."""
    require("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    codec = {
        "mp3": ["-codec:a", "libmp3lame", "-q:a", "2"],
        "m4a": ["-codec:a", "aac", "-b:a", "192k"],
        "wav": [],
    }.get(audio_format)
    if codec is None:
        raise ValueError(f"unsupported audio format: {audio_format}")
    _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), *codec, str(out_path)]
    )
    return out_path


def duration_seconds(media_path: Path) -> float:
    """Probe a rendered file so the video stage can match its length exactly."""
    probe = shutil.which("ffprobe")
    if probe is None:
        raise ToolMissing("`ffprobe` is not on PATH")
    result = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {result.stderr.strip()}")
    return float(result.stdout.strip())
