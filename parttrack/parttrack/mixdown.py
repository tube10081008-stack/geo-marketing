"""Turn one score into the set of per-part practice mixes.

Every mix is regenerated from the note model rather than patched out of the
source file, so the balance, timbre, count-in and tempo are fully determined by
the project config — the same input always yields the same output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

from .config import ProjectConfig
from .score import Score

# Channel 9 is reserved for percussion by the General MIDI spec; the click
# track lives there and voices route around it.
DRUM_CHANNEL = 9

LEVEL_LEAD = "lead"
LEVEL_BACKING = "backing"
LEVEL_MUTED = "muted"


@dataclass(frozen=True)
class MixSpec:
    """One deliverable: a variant, optionally focused on a single part."""

    variant: str
    lead_part_id: str | None
    tempo_label: str = ""
    tempo_scale: float = 1.0

    @property
    def slug(self) -> str:
        base = self.lead_part_id or "all"
        parts = [base, self.variant]
        if self.tempo_label:
            parts.append(self.tempo_label)
        return "-".join(parts)


def _meta_text(text: str, fallback: str) -> str:
    """MIDI meta strings are latin-1 only, so Korean names cannot go in them."""
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        stripped = "".join(char for char in text if char.isascii()).strip()
        return stripped or fallback


def plan_mixes(project: ProjectConfig) -> list[MixSpec]:
    """Expand the project's output config into the full list of deliverables."""
    specs: list[MixSpec] = []
    for label, scale in project.outputs.tempo_variants.items():
        for variant in project.outputs.variants:
            if variant == "full":
                specs.append(MixSpec("full", None, label, scale))
                continue
            for part in project.voice_parts:
                specs.append(MixSpec(variant, part.id, label, scale))
    return specs


def resolve_levels(project: ProjectConfig, spec: MixSpec) -> dict[str, str]:
    """Decide how loud each part sits in a given mix."""
    levels: dict[str, str] = {}
    for part in project.parts:
        if not part.is_voice:
            # Accompaniment (piano reduction, band track) is always present as
            # harmonic context but never competes with the voice being learned.
            levels[part.id] = LEVEL_BACKING
        elif spec.variant == "full":
            levels[part.id] = LEVEL_LEAD
        elif part.id == spec.lead_part_id:
            levels[part.id] = LEVEL_LEAD
        elif spec.variant == "part_only":
            levels[part.id] = LEVEL_MUTED
        else:
            levels[part.id] = LEVEL_BACKING
    return levels


def _assign_channels(part_ids: list[str]) -> dict[str, int]:
    channels: dict[str, int] = {}
    available = [ch for ch in range(16) if ch != DRUM_CHANNEL]
    if len(part_ids) > len(available):
        raise ValueError("too many parts for the 15 available MIDI channels")
    for part_id, channel in zip(part_ids, available):
        channels[part_id] = channel
    return channels


def _pan_positions(part_ids: list[str], lead_id: str | None, spread: int) -> dict[str, int]:
    """Centre the lead, fan the rest symmetrically around it."""
    others = [pid for pid in part_ids if pid != lead_id]
    positions: dict[str, int] = {}
    if lead_id is not None:
        positions[lead_id] = 64
    if not others:
        return positions
    if len(others) == 1:
        positions[others[0]] = 64
        return positions
    step = (2 * spread) / (len(others) - 1)
    for index, part_id in enumerate(others):
        positions[part_id] = max(0, min(127, int(round(64 - spread + step * index))))
    return positions


def _to_delta_track(events: list[tuple[int, int, mido.Message]]) -> mido.MidiTrack:
    """Sort absolute-time events and emit a delta-time track.

    The second tuple element is a priority so that, at any shared tick, meta and
    control messages land before note-offs, and note-offs before note-ons. That
    ordering keeps repeated pitches from swallowing each other.
    """
    track = mido.MidiTrack()
    previous = 0
    for absolute, _priority, message in sorted(
        events, key=lambda item: (item[0], item[1])
    ):
        track.append(message.copy(time=absolute - previous))
        previous = absolute
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def build_mix(project: ProjectConfig, score: Score, spec: MixSpec) -> mido.MidiFile:
    """Render one MixSpec into an in-memory MIDI file."""
    render = project.render
    levels = resolve_levels(project, spec)
    audible = [pid for pid, level in levels.items() if level != LEVEL_MUTED]
    if not audible:
        raise ValueError(f"mix {spec.slug} has no audible parts")

    channels = _assign_channels(audible)
    pans = _pan_positions(audible, spec.lead_part_id, render.pan_spread)
    shift = render.count_in_bars * score.ticks_per_bar

    midi = mido.MidiFile(type=1, ticks_per_beat=score.ticks_per_beat)

    # --- meta track -------------------------------------------------------
    meta_events: list[tuple[int, int, mido.Message]] = [
        (
            0,
            0,
            mido.MetaMessage(
                "track_name",
                name=_meta_text(f"{project.title} [{spec.slug}]", spec.slug),
                time=0,
            ),
        ),
        (
            0,
            0,
            mido.MetaMessage(
                "time_signature",
                numerator=score.numerator,
                denominator=score.denominator,
                time=0,
            ),
        ),
    ]
    scaled_tempo = score.tempo_map.scaled(spec.tempo_scale)
    for index, (tick, tempo) in enumerate(scaled_tempo.changes):
        # The count-in runs at the score's opening tempo, so the first tempo
        # event stays at tick 0 and later changes move with the music.
        position = 0 if index == 0 else tick + shift
        meta_events.append((position, 0, mido.MetaMessage("set_tempo", tempo=tempo, time=0)))
    midi.tracks.append(_to_delta_track(meta_events))

    # --- count-in click ---------------------------------------------------
    if render.count_in_bars > 0:
        click_events: list[tuple[int, int, mido.Message]] = [
            (0, 0, mido.MetaMessage("track_name", name="count-in", time=0))
        ]
        beats_per_bar = max(1, int(round(score.ticks_per_bar / score.ticks_per_beat)))
        total_beats = render.count_in_bars * beats_per_bar
        for beat in range(total_beats):
            at = beat * score.ticks_per_beat
            accent = beat % beats_per_bar == 0
            note = render.click_accent_note if accent else render.click_note
            velocity = render.click_velocity if accent else max(1, render.click_velocity - 22)
            click_events.append(
                (
                    at,
                    2,
                    mido.Message(
                        "note_on",
                        channel=DRUM_CHANNEL,
                        note=note,
                        velocity=velocity,
                        time=0,
                    ),
                )
            )
            click_events.append(
                (
                    at + score.ticks_per_beat // 2,
                    1,
                    mido.Message(
                        "note_off",
                        channel=DRUM_CHANNEL,
                        note=note,
                        velocity=0,
                        time=0,
                    ),
                )
            )
        midi.tracks.append(_to_delta_track(click_events))

    # --- one track per audible part ---------------------------------------
    for part in project.parts:
        level = levels[part.id]
        if level == LEVEL_MUTED:
            continue
        channel = channels[part.id]
        is_lead = level == LEVEL_LEAD
        program = render.lead_program if is_lead else render.backing_program
        volume = render.lead_volume if is_lead else render.backing_volume
        target_velocity = render.lead_velocity if is_lead else render.backing_velocity

        events: list[tuple[int, int, mido.Message]] = [
            (
                0,
                0,
                mido.MetaMessage(
                    "track_name", name=_meta_text(part.name or part.id, part.id), time=0
                ),
            ),
            (0, 0, mido.Message("program_change", channel=channel, program=program, time=0)),
            (0, 0, mido.Message("control_change", channel=channel, control=7, value=volume, time=0)),
            (
                0,
                0,
                mido.Message(
                    "control_change",
                    channel=channel,
                    control=10,
                    value=pans.get(part.id, 64),
                    time=0,
                ),
            ),
        ]

        for note in score.notes_for(part.id):
            # Preserve the score's own dynamic shaping while moving the part to
            # its target level, so accents survive the rebalance.
            velocity = int(round(target_velocity * (note.velocity / 100.0)))
            velocity = max(1, min(127, velocity))
            events.append(
                (
                    note.start_tick + shift,
                    2,
                    mido.Message(
                        "note_on",
                        channel=channel,
                        note=note.pitch,
                        velocity=velocity,
                        time=0,
                    ),
                )
            )
            events.append(
                (
                    note.end_tick + shift,
                    1,
                    mido.Message(
                        "note_off",
                        channel=channel,
                        note=note.pitch,
                        velocity=0,
                        time=0,
                    ),
                )
            )
        midi.tracks.append(_to_delta_track(events))

    return midi


def write_mix(
    project: ProjectConfig, score: Score, spec: MixSpec, destination: Path
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    build_mix(project, score, spec).save(str(destination))
    return destination


def count_in_seconds(project: ProjectConfig, score: Score, spec: MixSpec) -> float:
    """How much silence-plus-click precedes bar 1, in output time."""
    if project.render.count_in_bars <= 0:
        return 0.0
    scaled = score.tempo_map.scaled(spec.tempo_scale)
    return scaled.tick_to_second(project.render.count_in_bars * score.ticks_per_bar)
