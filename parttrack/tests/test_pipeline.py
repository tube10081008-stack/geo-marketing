"""Unit tests for the parts of the pipeline that need no external binaries."""

from __future__ import annotations

import sys
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parttrack.config import ProjectConfig  # noqa: E402
from parttrack.metadata import build_metadata  # noqa: E402
from parttrack.mixdown import (  # noqa: E402
    DRUM_CHANNEL,
    LEVEL_BACKING,
    LEVEL_LEAD,
    LEVEL_MUTED,
    MixSpec,
    build_mix,
    count_in_seconds,
    plan_mixes,
    resolve_levels,
)
from parttrack.score import load_score  # noqa: E402

TICKS_PER_BEAT = 480


def _write_source(path: Path, parts: int = 4, bars: int = 4) -> Path:
    """A trivial 4/4 score: one whole note per bar per part."""
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    midi.tracks.append(meta)

    for index in range(parts):
        track = mido.MidiTrack()
        for bar in range(bars):
            track.append(
                mido.Message(
                    "note_on",
                    channel=index,
                    note=72 - index * 5,
                    velocity=100,
                    time=0 if bar else 0,
                )
            )
            track.append(
                mido.Message(
                    "note_off",
                    channel=index,
                    note=72 - index * 5,
                    velocity=0,
                    time=TICKS_PER_BEAT * 4,
                )
            )
        midi.tracks.append(track)
    midi.save(str(path))
    return path


def _project(tmp_path: Path, **overrides) -> ProjectConfig:
    source = _write_source(tmp_path / "source.mid")
    raw = {
        "title": "테스트 곡",
        "work": "테스트 작품",
        "source": source.name,
        "rights": "public-domain",
        "parts": [
            {"id": "soprano", "name": "소프라노", "track": 1},
            {"id": "alto", "name": "알토", "track": 2},
            {"id": "tenor", "name": "테너", "track": 3},
            {"id": "bass", "name": "베이스", "track": 4},
        ],
    }
    raw.update(overrides)
    return ProjectConfig.from_dict(raw, root=tmp_path)


def test_score_binds_tracks_to_parts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)

    assert score.numerator == 4
    assert score.ticks_per_bar == TICKS_PER_BEAT * 4
    for part in project.parts:
        assert len(score.notes_for(part.id)) == 4
    # Four bars at 120 BPM in 4/4 is exactly eight seconds.
    assert score.duration_s == pytest.approx(8.0, abs=0.01)


def test_auto_track_assignment_follows_declaration_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    for part in project.parts:
        part.track = None
    score = load_score(project)

    # Track order is soprano..bass with descending pitches in the fixture.
    pitches = [score.notes_for(part.id)[0].pitch for part in project.parts]
    assert pitches == sorted(pitches, reverse=True)


def test_plan_covers_every_part_and_variant(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        outputs={
            "variants": ["per_part", "part_only", "full"],
            "tempo_variants": {"": 1.0, "느린템포": 0.8},
        },
    )
    specs = plan_mixes(project)
    # (4 parts x 2 focused variants + 1 full) x 2 tempi
    assert len(specs) == 18
    assert len({spec.slug for spec in specs}) == 18
    assert "bass-per_part-느린템포" in {spec.slug for spec in specs}


def test_levels_per_variant(tmp_path: Path) -> None:
    project = _project(tmp_path)

    focused = resolve_levels(project, MixSpec("per_part", "bass"))
    assert focused["bass"] == LEVEL_LEAD
    assert focused["soprano"] == LEVEL_BACKING

    solo = resolve_levels(project, MixSpec("part_only", "bass"))
    assert solo["bass"] == LEVEL_LEAD
    assert solo["soprano"] == LEVEL_MUTED

    full = resolve_levels(project, MixSpec("full", None))
    assert set(full.values()) == {LEVEL_LEAD}


def test_accompaniment_survives_part_only(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        parts=[
            {"id": "soprano", "name": "소프라노", "track": 1},
            {"id": "alto", "name": "알토", "track": 2},
            {"id": "tenor", "name": "테너", "track": 3},
            {"id": "piano", "name": "반주", "track": 4, "role": "accompaniment"},
        ],
    )
    levels = resolve_levels(project, MixSpec("part_only", "soprano"))
    assert levels["piano"] == LEVEL_BACKING
    assert levels["alto"] == LEVEL_MUTED


def test_mix_balances_and_voices_the_lead(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    midi = build_mix(project, score, MixSpec("per_part", "bass"))

    by_name = {
        next((m.name for m in track if m.type == "track_name"), ""): track
        for track in midi.tracks
    }
    lead = by_name["bass"]
    backing = by_name["soprano"]

    lead_programs = [m.program for m in lead if m.type == "program_change"]
    backing_programs = [m.program for m in backing if m.type == "program_change"]
    assert lead_programs == [project.render.lead_program]
    assert backing_programs == [project.render.backing_program]

    lead_volume = [m.value for m in lead if m.type == "control_change" and m.control == 7]
    backing_volume = [
        m.value for m in backing if m.type == "control_change" and m.control == 7
    ]
    assert lead_volume[0] > backing_volume[0]

    lead_velocity = max(m.velocity for m in lead if m.type == "note_on" and m.velocity)
    backing_velocity = max(
        m.velocity for m in backing if m.type == "note_on" and m.velocity
    )
    assert lead_velocity > backing_velocity


def test_part_only_drops_other_voices(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    midi = build_mix(project, score, MixSpec("part_only", "tenor"))

    names = {m.name for track in midi.tracks for m in track if m.type == "track_name"}
    assert "tenor" in names
    assert "soprano" not in names
    assert "alto" not in names


def test_count_in_shifts_music_and_adds_clicks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    spec = MixSpec("full", None)
    midi = build_mix(project, score, spec)

    click_track = next(
        track
        for track in midi.tracks
        if any(m.type == "note_on" and m.channel == DRUM_CHANNEL for m in track)
    )
    clicks = [m for m in click_track if m.type == "note_on" and m.velocity > 0]
    assert len(clicks) == 4  # one bar of 4/4

    voice = next(
        track
        for track in midi.tracks
        if any(m.type == "track_name" and m.name == "soprano" for m in track)
    )
    first_note_tick = 0
    for message in voice:
        first_note_tick += message.time
        if message.type == "note_on" and message.velocity > 0:
            break
    assert first_note_tick == score.ticks_per_bar

    assert count_in_seconds(project, score, spec) == pytest.approx(2.0, abs=0.01)


def test_zero_count_in_starts_immediately(tmp_path: Path) -> None:
    project = _project(tmp_path, render={"count_in_bars": 0})
    score = load_score(project)
    spec = MixSpec("full", None)
    assert count_in_seconds(project, score, spec) == 0.0

    midi = build_mix(project, score, spec)
    assert not any(
        m.type == "note_on" and m.channel == DRUM_CHANNEL
        for track in midi.tracks
        for m in track
    )


def test_tempo_scaling_slows_playback(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    slow = score.tempo_map.scaled(0.5)
    end = score.duration_tick
    assert slow.tick_to_second(end) == pytest.approx(score.duration_s * 2, rel=1e-6)

    midi = build_mix(project, score, MixSpec("full", None, "느린템포", 0.5))
    tempos = [m.tempo for track in midi.tracks for m in track if m.type == "set_tempo"]
    assert tempos and all(
        tempo == pytest.approx(mido.bpm2tempo(120) / 0.5, rel=1e-3) for tempo in tempos
    )


def test_channels_avoid_the_drum_channel(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    midi = build_mix(project, score, MixSpec("full", None))

    voice_channels = {
        m.channel
        for track in midi.tracks
        for m in track
        if m.type == "note_on" and not any(x.type == "track_name" and x.name == "count-in" for x in track)
    }
    assert DRUM_CHANNEL not in voice_channels


def test_metadata_is_part_specific(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    spec = MixSpec("per_part", "bass")
    metadata = build_metadata(project, score, spec, count_in_s=2.0)

    assert "베이스" in metadata.title
    assert "테스트 곡" in metadata.title
    assert "베이스" in metadata.description
    assert "퍼블릭 도메인" in metadata.description
    assert "테스트 곡 베이스" in metadata.tags
    assert metadata.chapters[0] == (0.0, "카운트인")
    assert len(metadata.tags) == len(set(metadata.tags))


def test_metadata_marks_slow_variant(tmp_path: Path) -> None:
    project = _project(tmp_path)
    score = load_score(project)
    metadata = build_metadata(
        project, score, MixSpec("per_part", "alto", "느린템포", 0.8), count_in_s=2.0
    )
    assert "느린템포" in metadata.title
    assert "80%" in metadata.description


def test_missing_source_is_reported(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.source = Path("nope.mid")
    with pytest.raises(FileNotFoundError):
        load_score(project)


def test_invalid_variant_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        _project(tmp_path, outputs={"variants": ["bogus"]})
