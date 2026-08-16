"""Unit tests for the parts of the pipeline that need no external binaries."""

from __future__ import annotations

import sys
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parttrack.config import ProjectConfig, SectionConfig  # noqa: E402
from parttrack.rehearsal import (  # noqa: E402
    _atempo_chain,
    bar_to_time,
    local_seconds_per_bar,
    section_slug,
)
from parttrack.metadata import build_metadata  # noqa: E402
from parttrack.mixdown import (  # noqa: E402
    DRUM_CHANNEL,
    LEVEL_BACKING,
    LEVEL_LEAD,
    LEVEL_MUTED,
    LEVEL_REFERENCE,
    MixSpec,
    build_mix,
    count_in_seconds,
    plan_mixes,
    resolve_levels,
)
from parttrack.score import load_score  # noqa: E402
from parttrack.timing import (  # noqa: E402
    bar_tick,
    effective_tempo_map,
    marker_points,
    running_click_ticks,
    section_spans,
)
from parttrack.timing import count_in_seconds as timing_count_in  # noqa: E402

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


def _project(tmp_path: Path, bars: int = 4, **overrides) -> ProjectConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _write_source(tmp_path / "source.mid", bars=bars)
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
        if m.type == "note_on"
        and not any(x.type == "track_name" and x.name == "click" for x in track)
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
    assert len(metadata.tags) == len(set(metadata.tags))


def test_short_score_emits_no_chapters(tmp_path: Path) -> None:
    """YouTube ignores chapter lists under three entries, so we omit them."""
    project = _project(tmp_path, bars=4)  # eight seconds total
    score = load_score(project)
    metadata = build_metadata(
        project, score, MixSpec("per_part", "bass"), count_in_s=2.0
    )
    assert metadata.chapters == []


def test_long_score_gets_spaced_chapters(tmp_path: Path) -> None:
    project = _project(tmp_path, bars=40)  # eighty seconds
    score = load_score(project)
    metadata = build_metadata(
        project, score, MixSpec("per_part", "bass"), count_in_s=2.0
    )
    assert metadata.chapters[0] == (0.0, "카운트인")
    assert len(metadata.chapters) >= 3
    gaps = [
        b[0] - a[0] for a, b in zip(metadata.chapters, metadata.chapters[1:])
    ]
    assert all(gap >= 10.0 for gap in gaps)


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


# --- reference parts -------------------------------------------------------


def _ballad_project(tmp_path: Path, bars: int = 8, **overrides) -> ProjectConfig:
    """A solo cue line plus three backing voices, like a theatre ballad."""
    defaults = {
        "parts": [
            {"id": "solo", "name": "솔로", "track": 1, "role": "reference"},
            {"id": "alto", "name": "알토", "track": 2},
            {"id": "tenor", "name": "테너", "track": 3},
            {"id": "bass", "name": "베이스", "track": 4},
        ]
    }
    defaults.update(overrides)
    return _project(tmp_path, bars=bars, **defaults)


def test_reference_part_is_never_a_practice_target(tmp_path: Path) -> None:
    project = _ballad_project(tmp_path)
    specs = plan_mixes(project)
    assert "solo" not in {spec.lead_part_id for spec in specs}
    # three voices x per_part, plus one full mix
    assert len(specs) == 4


def test_reference_part_survives_every_variant(tmp_path: Path) -> None:
    project = _ballad_project(tmp_path)
    for spec in (
        MixSpec("per_part", "alto"),
        MixSpec("part_only", "alto"),
        MixSpec("full", None),
    ):
        levels = resolve_levels(project, spec)
        assert levels["solo"] == LEVEL_REFERENCE, spec.variant


def test_reference_part_gets_its_own_voice(tmp_path: Path) -> None:
    project = _ballad_project(tmp_path)
    score = load_score(project)
    midi = build_mix(project, score, MixSpec("part_only", "alto"))

    tracks = {
        next((m.name for m in track if m.type == "track_name"), ""): track
        for track in midi.tracks
    }
    solo = tracks["solo"]
    programs = [m.program for m in solo if m.type == "program_change"]
    volumes = [m.value for m in solo if m.type == "control_change" and m.control == 7]
    assert programs == [project.render.reference_program]
    assert volumes == [project.render.reference_volume]
    # Audible above the muted voices but below the part being learned.
    assert project.render.backing_volume < volumes[0] < project.render.lead_volume


def test_project_without_voice_parts_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="role 'voice'"):
        _project(
            tmp_path,
            parts=[
                {"id": "solo", "name": "솔로", "track": 1, "role": "reference"},
                {"id": "piano", "name": "반주", "track": 2, "role": "accompaniment"},
            ],
        )


# --- sections and markers --------------------------------------------------


def test_section_tempo_override_lands_on_its_barline(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        bars=8,
        sections=[{"name": "엔딩", "from_bar": 5, "to_bar": 8, "tempo_scale": 0.5}],
    )
    score = load_score(project)
    tempo_map = effective_tempo_map(project, score)

    boundary = bar_tick(score, 5)
    assert boundary == score.ticks_per_bar * 4
    # Half speed means twice as many microseconds per beat.
    assert tempo_map.tempo_at(boundary - 1) == pytest.approx(mido.bpm2tempo(120), rel=1e-3)
    assert tempo_map.tempo_at(boundary) == pytest.approx(
        mido.bpm2tempo(120) / 0.5, rel=1e-3
    )


def test_section_tempo_stretches_output_duration(tmp_path: Path) -> None:
    plain = _project(tmp_path / "a", bars=8)
    slowed = _project(
        tmp_path / "b",
        bars=8,
        sections=[{"name": "엔딩", "from_bar": 5, "to_bar": 8, "tempo_scale": 0.5}],
    )
    plain_score, slowed_score = load_score(plain), load_score(slowed)
    end = plain_score.duration_tick

    plain_end = effective_tempo_map(plain, plain_score).tick_to_second(end)
    slowed_end = effective_tempo_map(slowed, slowed_score).tick_to_second(end)
    # The second half takes twice as long; the first half is untouched.
    assert slowed_end == pytest.approx(plain_end * 1.5, rel=1e-3)


def test_click_is_off_by_default_and_on_when_asked(tmp_path: Path) -> None:
    quiet = _project(tmp_path / "a", bars=8)
    assert running_click_ticks(quiet, load_score(quiet)) == []

    loud = _project(tmp_path / "b", bars=8, render={"click_through": True})
    score = load_score(loud)
    clicks = running_click_ticks(loud, score)
    assert len(clicks) == 8 * 4  # eight bars of 4/4
    assert clicks[0] == (0, True)
    assert clicks[1][1] is False


def test_rubato_section_silences_the_click(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        bars=8,
        render={"click_through": True},
        sections=[
            {"name": "콜라보체", "from_bar": 1, "to_bar": 4, "rubato": True},
            {"name": "본진행", "from_bar": 5, "to_bar": 8},
        ],
    )
    score = load_score(project)
    clicks = running_click_ticks(project, score)

    assert len(clicks) == 16  # only the second half
    assert min(tick for tick, _ in clicks) == bar_tick(score, 5)


def test_section_can_opt_into_click_when_default_is_off(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        bars=8,
        sections=[{"name": "후렴", "from_bar": 5, "to_bar": 8, "click": True}],
    )
    score = load_score(project)
    clicks = running_click_ticks(project, score)
    assert len(clicks) == 16
    assert all(tick >= bar_tick(score, 5) for tick, _ in clicks)


def test_running_click_lands_on_the_drum_channel(tmp_path: Path) -> None:
    project = _project(tmp_path, bars=8, render={"click_through": True})
    score = load_score(project)
    midi = build_mix(project, score, MixSpec("full", None))

    click_track = next(
        track
        for track in midi.tracks
        if any(m.type == "track_name" and m.name == "click" for m in track)
    )
    hits = [m for m in click_track if m.type == "note_on" and m.velocity > 0]
    # One count-in bar plus eight bars of running click, all on channel 9.
    assert len(hits) == 4 + 32
    assert {m.channel for m in hits} == {DRUM_CHANNEL}


def test_sections_drive_chapters(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        bars=40,
        sections=[
            {"name": "1절", "from_bar": 1, "to_bar": 20},
            {"name": "후렴", "from_bar": 21, "to_bar": 40},
        ],
        markers=[{"bar": 21, "label": "전조 +1"}],
    )
    score = load_score(project)
    metadata = build_metadata(
        project, score, MixSpec("per_part", "bass"), count_in_s=2.0
    )
    labels = [label for _, label in metadata.chapters]
    assert any("1절" in label for label in labels)
    assert any("후렴" in label for label in labels)
    # The marker shares bar 21 with the section, so thinning keeps just one.
    assert len(metadata.chapters) == len(set(labels))


def test_section_spans_report_output_times(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        bars=8,
        sections=[
            {"name": "전반", "from_bar": 1, "to_bar": 4},
            {"name": "후반", "from_bar": 5, "to_bar": 8},
        ],
    )
    score = load_score(project)
    spans = section_spans(project, score)
    count_in = timing_count_in(project, score)

    assert [span.name for span in spans] == ["전반", "후반"]
    assert spans[0].start_s == pytest.approx(count_in, abs=0.01)
    # Four bars of 4/4 at 120 BPM is eight seconds.
    assert spans[1].start_s == pytest.approx(count_in + 8.0, abs=0.01)


def test_marker_beyond_the_score_is_reported(tmp_path: Path) -> None:
    project = _project(tmp_path, bars=4, markers=[{"bar": 99, "label": "없는 마디"}])
    score = load_score(project)
    with pytest.raises(ValueError, match="only has 4 bars"):
        marker_points(project, score)


def test_overlapping_sections_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlap"):
        _project(
            tmp_path,
            sections=[
                {"name": "A", "from_bar": 1, "to_bar": 8},
                {"name": "B", "from_bar": 5, "to_bar": 12},
            ],
        )


def test_backwards_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="to_bar precedes from_bar"):
        _project(tmp_path, sections=[{"name": "A", "from_bar": 8, "to_bar": 4}])


# --- rehearsal kit from a real recording -----------------------------------

RECORDING = {
    "source": "take.mp3",
    "beats_per_bar": 2,
    "anchors": [
        {"bar": 1, "at": 0.0},
        {"bar": 25, "at": 40.0},
        {"bar": 49, "at": 76.0},
    ],
}


def test_bar_to_time_interpolates_between_anchors(tmp_path: Path) -> None:
    project = _project(tmp_path, recording=RECORDING)
    recording = project.recording

    assert bar_to_time(recording, 1) == pytest.approx(0.0)
    assert bar_to_time(recording, 25) == pytest.approx(40.0)
    assert bar_to_time(recording, 49) == pytest.approx(76.0)
    # Halfway through the first span, in bars, is halfway through it in seconds.
    assert bar_to_time(recording, 13) == pytest.approx(20.0)
    # The second span runs at a different rate; interpolation follows it.
    assert bar_to_time(recording, 37) == pytest.approx(58.0)


def test_bar_to_time_extrapolates_past_the_ends(tmp_path: Path) -> None:
    project = _project(tmp_path, recording=RECORDING)
    recording = project.recording
    # 1.5s per bar in the closing span carries past the last anchor.
    assert bar_to_time(recording, 53) == pytest.approx(82.0)
    assert bar_to_time(recording, -3) < 0.0


def test_local_seconds_per_bar_tracks_the_span(tmp_path: Path) -> None:
    project = _project(tmp_path, recording=RECORDING)
    recording = project.recording
    assert local_seconds_per_bar(recording, 5) == pytest.approx(40.0 / 24)
    assert local_seconds_per_bar(recording, 30) == pytest.approx(36.0 / 24)


def test_recording_needs_two_anchors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least two anchors"):
        _project(
            tmp_path,
            recording={"source": "take.mp3", "anchors": [{"bar": 1, "at": 0.0}]},
        )


def test_recording_anchor_times_must_increase(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must increase"):
        _project(
            tmp_path,
            recording={
                "source": "take.mp3",
                "anchors": [{"bar": 1, "at": 30.0}, {"bar": 9, "at": 10.0}],
            },
        )


def test_recording_only_project_needs_no_parts(tmp_path: Path) -> None:
    """A kit cut from audio alone never loads a score, so parts are optional."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig.from_dict(
        {
            "title": "실황",
            "recording": RECORDING,
            "sections": [{"name": "후렴", "from_bar": 25, "to_bar": 48}],
        },
        root=tmp_path,
    )
    assert project.parts == []
    assert project.recording is not None
    assert len(project.sections) == 1


def test_recording_only_project_still_checks_sections(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlap"):
        ProjectConfig.from_dict(
            {
                "title": "실황",
                "recording": RECORDING,
                "sections": [
                    {"name": "A", "from_bar": 1, "to_bar": 10},
                    {"name": "B", "from_bar": 5, "to_bar": 20},
                ],
            },
            root=tmp_path,
        )


def test_atempo_chain_stays_within_ffmpeg_limits(tmp_path: Path) -> None:
    for factor in (0.9, 0.75, 0.5, 0.4, 0.25, 1.5):
        chain = _atempo_chain(factor)
        assert all(0.5 <= value <= 2.0 for value in chain), factor
        product = 1.0
        for value in chain:
            product *= value
        assert product == pytest.approx(factor, rel=1e-4)


def test_section_slug_is_filesystem_safe(tmp_path: Path) -> None:
    ascii_section = SectionConfig(name="Power Ballad", from_bar=25, to_bar=32)
    korean_section = SectionConfig(name="앙상블 진입", from_bar=33, to_bar=40)

    assert section_slug(ascii_section) == "power-ballad-25-32"
    # Nothing usable survives from a Korean name, so bars identify the file.
    assert section_slug(korean_section) == "bars-33-40"
    for slug in (section_slug(ascii_section), section_slug(korean_section)):
        assert slug.isascii() and "/" not in slug and " " not in slug
