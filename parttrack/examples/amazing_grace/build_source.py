"""Generate the public-domain demo score: a simple SATB setting of 'Amazing Grace'.

The tune (NEW BRITAIN, 1835) and this four-part setting are both in the public
domain, which makes it a safe fixture for validating the pipeline end to end
before any licensed material is involved.

Run:  python3 examples/amazing_grace/build_source.py
"""

from __future__ import annotations

from pathlib import Path

import mido

TICKS_PER_BEAT = 480
BPM = 84
BEATS_PER_BAR = 3

# Pitch classes for the three chords this setting uses, in G major.
CHORDS = {
    "G": (7, 11, 2),   # G  B  D
    "C": (0, 4, 7),    # C  E  G
    "D": (2, 6, 9),    # D  F# A
}

# Comfortable amateur ranges, in MIDI note numbers.
RANGES = {
    "soprano": (60, 79),
    "alto": (55, 72),
    "tenor": (48, 67),
    "bass": (40, 60),
}

# (soprano pitch, beats, chord) per bar. Sixteen bars, no anacrusis, so every
# phrase lines up with a barline and the count-in lands cleanly.
MELODY: list[list[tuple[int, float, str]]] = [
    [(67, 2, "G"), (71, 1, "G")],            # 1  A-maz-ing
    [(67, 2, "G"), (71, 1, "G")],            # 2  grace, how
    [(69, 2, "D"), (67, 1, "G")],            # 3  sweet the
    [(64, 1, "C"), (62, 2, "G")],            # 4  sound
    [(67, 2, "G"), (71, 1, "G")],            # 5  that saved
    [(67, 2, "G"), (71, 1, "G")],            # 6  a wretch
    [(69, 2, "D"), (74, 1, "D")],            # 7  like me
    [(71, 1, "G"), (67, 2, "G")],            # 8
    [(74, 2, "G"), (71, 1, "G")],            # 9  I once was
    [(74, 2, "G"), (71, 1, "G")],            # 10 lost, but now
    [(69, 2, "D"), (67, 1, "G")],            # 11 am found
    [(64, 1, "C"), (62, 2, "G")],            # 12
    [(67, 2, "G"), (71, 1, "G")],            # 13 was blind
    [(67, 2, "G"), (69, 1, "D")],            # 14 but now
    [(71, 2, "G"), (69, 1, "D")],            # 15 I
    [(67, 3, "G")],                          # 16 see
]

PART_ORDER = ("soprano", "alto", "tenor", "bass")


def _pick(pitch_classes: tuple[int, ...], ceiling: int, low: int, high: int) -> int:
    """Highest pitch in [low, high] below `ceiling` whose class is in the chord."""
    for pitch in range(min(high, ceiling - 1), low - 1, -1):
        if pitch % 12 in pitch_classes:
            return pitch
    # Nothing fits under the ceiling, so take the lowest available chord tone.
    for pitch in range(low, high + 1):
        if pitch % 12 in pitch_classes:
            return pitch
    raise ValueError("no chord tone in range")


def voice_chord(soprano: int, chord: str) -> dict[str, int]:
    """Close-position SATB voicing under a given melody note."""
    pitch_classes = CHORDS[chord]
    root_class = pitch_classes[0]

    bass_low, bass_high = RANGES["bass"]
    bass = next(
        pitch
        for pitch in range(bass_low, bass_high + 1)
        if pitch % 12 == root_class
    )

    alto = _pick(pitch_classes, soprano, *RANGES["alto"])
    tenor = _pick(pitch_classes, alto, *RANGES["tenor"])
    return {"soprano": soprano, "alto": alto, "tenor": tenor, "bass": bass}


def build() -> mido.MidiFile:
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Amazing Grace (SATB)", time=0))
    meta.append(
        mido.MetaMessage(
            "time_signature", numerator=3, denominator=4, time=0
        )
    )
    meta.append(mido.MetaMessage("key_signature", key="G", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
    midi.tracks.append(meta)

    # Collect (start_tick, duration_tick, pitch) per part, then emit each track.
    lines: dict[str, list[tuple[int, int, int]]] = {name: [] for name in PART_ORDER}
    cursor = 0
    for bar in MELODY:
        for soprano, beats, chord in bar:
            duration = int(beats * TICKS_PER_BEAT)
            voicing = voice_chord(soprano, chord)
            for name in PART_ORDER:
                lines[name].append((cursor, duration, voicing[name]))
            cursor += duration

    for index, name in enumerate(PART_ORDER):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name.capitalize(), time=0))
        previous = 0
        for start, duration, pitch in lines[name]:
            # A touch of detachment keeps repeated pitches from slurring.
            sounding = max(1, int(duration * 0.94))
            track.append(
                mido.Message(
                    "note_on",
                    channel=index,
                    note=pitch,
                    velocity=100,
                    time=start - previous,
                )
            )
            track.append(
                mido.Message(
                    "note_off", channel=index, note=pitch, velocity=0, time=sounding
                )
            )
            previous = start + sounding
        track.append(mido.MetaMessage("end_of_track", time=0))
        midi.tracks.append(track)

    return midi


def main() -> None:
    destination = Path(__file__).parent / "amazing_grace.mid"
    build().save(str(destination))
    bars = len(MELODY)
    seconds = bars * BEATS_PER_BAR * 60 / BPM
    print(f"wrote {destination} ({bars} bars, ~{seconds:.0f}s at {BPM} BPM)")


if __name__ == "__main__":
    main()
