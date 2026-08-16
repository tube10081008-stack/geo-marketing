"""Public-domain fixture shaped like a musical-theatre power ballad.

Real theatre numbers are not four-part hymns. They open with a solo over free
tempo, bring backing vocals in partway through, modulate for the last section
and ritard into the button. This fixture reproduces that shape using
public-domain material (the NEW BRITAIN tune) so the section, marker and
reference-part features can be validated without touching licensed scores.

Structure (16 bars, 3/4):
  bars  1-8   solo only, colla voce
  bars  9-12  backing vocals enter on sustained pads
  bars 13-16  modulation up a semitone, ritard into the ending

Run:  python3 examples/ballad_form/build_source.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "amazing_grace"))

from build_source import (  # noqa: E402  (path is set up immediately above)
    BPM,
    MELODY,
    TICKS_PER_BEAT,
    voice_chord,
)

BACKING_FROM_BAR = 9
MODULATION_BAR = 13
MODULATION_SEMITONES = 1

BACKING_PARTS = ("alto", "tenor", "bass")
# Pads sit an octave below the melody so they support rather than compete.
BACKING_OCTAVE_SHIFT = 0


def _shift_for_bar(bar_number: int) -> int:
    return MODULATION_SEMITONES if bar_number >= MODULATION_BAR else 0


def _collect() -> dict[str, list[tuple[int, int, int]]]:
    """(start_tick, duration_tick, pitch) per track name."""
    lines: dict[str, list[tuple[int, int, int]]] = {
        "solo": [],
        "alto": [],
        "tenor": [],
        "bass": [],
        "piano": [],
    }

    cursor = 0
    for index, bar in enumerate(MELODY):
        bar_number = index + 1
        shift = _shift_for_bar(bar_number)
        bar_start = cursor
        bar_length = 0

        for soprano, beats, chord in bar:
            duration = int(beats * TICKS_PER_BEAT)
            lines["solo"].append((cursor, duration, soprano + shift))
            cursor += duration
            bar_length += duration

        # One sustained pad per bar, voiced from the bar's opening harmony.
        first_chord = bar[0][2]
        voicing = voice_chord(bar[0][0], first_chord)
        if bar_number >= BACKING_FROM_BAR:
            for name in BACKING_PARTS:
                lines[name].append(
                    (bar_start, bar_length, voicing[name] + shift + BACKING_OCTAVE_SHIFT)
                )

        # Piano keeps root and fifth under everything, including the solo verse.
        root = voicing["bass"] + shift
        lines["piano"].append((bar_start, bar_length, root - 12))
        lines["piano"].append((bar_start, bar_length, root - 5))

    return lines


TRACK_ORDER = ("solo", "alto", "tenor", "bass", "piano")


def build() -> mido.MidiFile:
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Ballad Form (PD fixture)", time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
    midi.tracks.append(meta)

    lines = _collect()
    for index, name in enumerate(TRACK_ORDER):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))

        events: list[tuple[int, int, mido.Message]] = []
        for start, duration, pitch in lines[name]:
            sounding = max(1, int(duration * 0.94))
            events.append(
                (
                    start,
                    1,
                    mido.Message(
                        "note_on", channel=index, note=pitch, velocity=100, time=0
                    ),
                )
            )
            events.append(
                (
                    start + sounding,
                    0,
                    mido.Message(
                        "note_off", channel=index, note=pitch, velocity=0, time=0
                    ),
                )
            )

        previous = 0
        for absolute, _priority, message in sorted(
            events, key=lambda item: (item[0], item[1])
        ):
            track.append(message.copy(time=absolute - previous))
            previous = absolute
        track.append(mido.MetaMessage("end_of_track", time=0))
        midi.tracks.append(track)

    return midi


def main() -> None:
    destination = Path(__file__).parent / "ballad_form.mid"
    build().save(str(destination))
    print(
        f"wrote {destination} ({len(MELODY)} bars, backing from bar "
        f"{BACKING_FROM_BAR}, modulation at bar {MODULATION_BAR})"
    )


if __name__ == "__main__":
    main()
