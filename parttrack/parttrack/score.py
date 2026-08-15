"""Read a MIDI score into a part-aware note model.

The pipeline is deliberately score-driven: every downstream artefact (mix,
audio, video) is derived from the notes read here, so what a singer hears is
exactly what the score says. Nothing is generated or inferred.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import mido

from .config import PartConfig, ProjectConfig

DEFAULT_TEMPO = 500_000  # microseconds per beat == 120 BPM


@dataclass
class Note:
    part_id: str
    pitch: int
    velocity: int
    start_tick: int
    end_tick: int
    start_s: float = 0.0
    end_s: float = 0.0

    @property
    def duration_tick(self) -> int:
        return self.end_tick - self.start_tick

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class TempoMap:
    """Tick <-> second conversion across an arbitrary number of tempo changes."""

    def __init__(self, ticks_per_beat: int, changes: list[tuple[int, int]]):
        self.ticks_per_beat = ticks_per_beat
        if not changes or changes[0][0] != 0:
            changes = [(0, DEFAULT_TEMPO)] + [c for c in changes if c[0] != 0]
        self.changes = sorted(changes, key=lambda item: item[0])

        # Precompute the elapsed seconds at each tempo change so lookups are
        # O(log n) rather than a walk from the top of the file.
        self._tick_marks: list[int] = []
        self._second_marks: list[float] = []
        elapsed = 0.0
        previous_tick, previous_tempo = self.changes[0]
        for index, (tick, tempo) in enumerate(self.changes):
            if index > 0:
                elapsed += mido.tick2second(
                    tick - previous_tick, ticks_per_beat, previous_tempo
                )
                previous_tick, previous_tempo = tick, tempo
            self._tick_marks.append(tick)
            self._second_marks.append(elapsed)

    def tempo_at(self, tick: int) -> int:
        index = bisect.bisect_right(self._tick_marks, tick) - 1
        return self.changes[max(index, 0)][1]

    def tick_to_second(self, tick: int) -> float:
        index = max(bisect.bisect_right(self._tick_marks, tick) - 1, 0)
        base_tick = self._tick_marks[index]
        base_seconds = self._second_marks[index]
        tempo = self.changes[index][1]
        return base_seconds + mido.tick2second(
            tick - base_tick, self.ticks_per_beat, tempo
        )

    def scaled(self, factor: float) -> "TempoMap":
        """Return a tempo map running at ``factor`` times the written speed."""
        if factor <= 0:
            raise ValueError("tempo factor must be > 0")
        return TempoMap(
            self.ticks_per_beat,
            [(tick, int(round(tempo / factor))) for tick, tempo in self.changes],
        )


@dataclass
class Score:
    ticks_per_beat: int
    notes: list[Note]
    parts: list[PartConfig]
    tempo_map: TempoMap
    numerator: int = 4
    denominator: int = 4
    key_signature: str = ""

    # Cached lookups, filled by ``_index``.
    _by_part: dict[str, list[Note]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.notes.sort(key=lambda note: (note.start_tick, note.pitch))
        self._by_part = {part.id: [] for part in self.parts}
        for note in self.notes:
            self._by_part.setdefault(note.part_id, []).append(note)

    def notes_for(self, part_id: str) -> list[Note]:
        return self._by_part.get(part_id, [])

    @property
    def duration_tick(self) -> int:
        return max((note.end_tick for note in self.notes), default=0)

    @property
    def duration_s(self) -> float:
        return self.tempo_map.tick_to_second(self.duration_tick)

    @property
    def ticks_per_bar(self) -> int:
        beats_per_bar = self.numerator * (4.0 / self.denominator)
        return int(round(self.ticks_per_beat * beats_per_bar))

    @property
    def pitch_range(self) -> tuple[int, int]:
        if not self.notes:
            return (60, 72)
        pitches = [note.pitch for note in self.notes]
        return (min(pitches), max(pitches))

    def bar_ticks(self) -> list[int]:
        """Tick positions of every barline up to the end of the score."""
        step = self.ticks_per_bar
        if step <= 0:
            return []
        return list(range(0, self.duration_tick + step, step))


def _track_note_events(track: mido.MidiTrack) -> list[tuple[int, mido.Message]]:
    absolute = 0
    events: list[tuple[int, mido.Message]] = []
    for message in track:
        absolute += message.time
        events.append((absolute, message))
    return events


def load_score(project: ProjectConfig) -> Score:
    """Load the project's source MIDI and bind its tracks to declared parts."""
    path = project.source_path
    if not path.exists():
        raise FileNotFoundError(f"source score not found: {path}")

    midi = mido.MidiFile(str(path))
    tempo_changes: list[tuple[int, int]] = []
    numerator, denominator = 4, 4
    key_signature = ""

    for track in midi.tracks:
        for absolute, message in _track_note_events(track):
            if message.type == "set_tempo":
                tempo_changes.append((absolute, message.tempo))
            elif message.type == "time_signature" and absolute == 0:
                numerator, denominator = message.numerator, message.denominator
            elif message.type == "key_signature" and absolute == 0:
                key_signature = message.key

    tempo_map = TempoMap(midi.ticks_per_beat, tempo_changes)
    parts_by_track = _resolve_part_tracks(project, midi)

    notes: list[Note] = []
    for track_index, track in enumerate(midi.tracks):
        part = parts_by_track.get(track_index)
        if part is None:
            continue
        open_notes: dict[int, list[tuple[int, int]]] = {}
        for absolute, message in _track_note_events(track):
            if message.type == "note_on" and message.velocity > 0:
                open_notes.setdefault(message.note, []).append(
                    (absolute, message.velocity)
                )
            elif message.type == "note_off" or (
                message.type == "note_on" and message.velocity == 0
            ):
                pending = open_notes.get(message.note)
                if not pending:
                    continue
                start_tick, velocity = pending.pop(0)
                if absolute <= start_tick:
                    continue
                notes.append(
                    Note(
                        part_id=part.id,
                        pitch=message.note,
                        velocity=velocity,
                        start_tick=start_tick,
                        end_tick=absolute,
                    )
                )

    if not notes:
        raise ValueError(
            f"no notes found in {path}; check that the 'track' indices in the "
            "project file point at the tracks that actually contain notes"
        )

    for note in notes:
        note.start_s = tempo_map.tick_to_second(note.start_tick)
        note.end_s = tempo_map.tick_to_second(note.end_tick)

    return Score(
        ticks_per_beat=midi.ticks_per_beat,
        notes=notes,
        parts=project.parts,
        tempo_map=tempo_map,
        numerator=numerator,
        denominator=denominator,
        key_signature=key_signature,
    )


def _resolve_part_tracks(
    project: ProjectConfig, midi: mido.MidiFile
) -> dict[int, PartConfig]:
    """Map source track index -> part, filling in unset indices in order."""
    note_tracks = [
        index
        for index, track in enumerate(midi.tracks)
        if any(message.type == "note_on" for message in track)
    ]

    mapping: dict[int, PartConfig] = {}
    unassigned: list[PartConfig] = []
    for part in project.parts:
        if part.track is None:
            unassigned.append(part)
            continue
        if part.track >= len(midi.tracks):
            raise ValueError(
                f"part {part.id!r} points at track {part.track} but the source "
                f"only has {len(midi.tracks)} tracks"
            )
        mapping[part.track] = part

    free_tracks = [index for index in note_tracks if index not in mapping]
    if len(unassigned) > len(free_tracks):
        raise ValueError(
            f"{len(unassigned)} parts have no 'track' set but only "
            f"{len(free_tracks)} unclaimed note tracks are available"
        )
    for part, track_index in zip(unassigned, free_tracks):
        mapping[track_index] = part
    return mapping


def describe(score: Score) -> str:
    """Human-readable summary used by the CLI."""
    low, high = score.pitch_range
    lines = [
        f"ticks/beat : {score.ticks_per_beat}",
        f"time sig   : {score.numerator}/{score.denominator}",
        f"bars       : {len(score.bar_ticks()) - 1}",
        f"duration   : {score.duration_s:.1f}s",
        f"pitch range: {low}-{high}",
        "parts:",
    ]
    for part in score.parts:
        part_notes = score.notes_for(part.id)
        if not part_notes:
            lines.append(f"  - {part.id:<12} (no notes)")
            continue
        pitches = [note.pitch for note in part_notes]
        lines.append(
            f"  - {part.id:<12} {len(part_notes):>4} notes  "
            f"range {min(pitches)}-{max(pitches)}"
        )
    return "\n".join(lines)


def load_from_path(path: str | Path, project: ProjectConfig) -> Score:
    """Convenience wrapper used by tests that swap in a different source file."""
    project.source = Path(path)
    return load_score(project)
