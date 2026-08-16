"""Shared timing model: where every bar, section and marker lands in output time.

Mixdown, video and metadata all need the same answer to "when does bar 49
happen?". Deriving it in one place keeps the click, the scrolling roll and the
chapter list from drifting apart once section tempo overrides are involved.
"""

from __future__ import annotations

from dataclasses import dataclass

import mido

from .config import ProjectConfig
from .score import Score, TempoMap


@dataclass(frozen=True)
class SectionSpan:
    """A configured section resolved against the score's clock."""

    name: str
    from_bar: int
    to_bar: int
    start_tick: int
    end_tick: int
    start_s: float
    end_s: float
    tempo_scale: float
    click: bool
    rubato: bool


@dataclass(frozen=True)
class MarkerPoint:
    bar: int
    label: str
    tick: int
    at_s: float
    rgb: tuple[int, int, int]


def bar_tick(score: Score, bar: int) -> int:
    """Tick at which a 1-indexed bar begins."""
    return max(0, bar - 1) * score.ticks_per_bar


def bar_of_tick(score: Score, tick: int) -> int:
    if score.ticks_per_bar <= 0:
        return 1
    return tick // score.ticks_per_bar + 1


def section_scale_at_bar(project: ProjectConfig, bar: int) -> float:
    section = project.section_at_bar(bar)
    return section.tempo_scale if section else 1.0


def effective_tempo_map(
    project: ProjectConfig, score: Score, tempo_scale: float = 1.0
) -> TempoMap:
    """Combine the score's own tempo map with the variant and section scales."""
    base = score.tempo_map
    global_scale = tempo_scale

    boundaries: set[int] = {0}
    boundaries.update(tick for tick, _ in base.changes)
    for section in project.sections:
        boundaries.add(bar_tick(score, section.from_bar))
        boundaries.add(bar_tick(score, section.to_bar + 1))

    duration = score.duration_tick
    changes: list[tuple[int, int]] = []
    for tick in sorted(boundaries):
        if tick > duration:
            continue
        scale = global_scale * section_scale_at_bar(project, bar_of_tick(score, tick))
        changes.append((tick, int(round(base.tempo_at(tick) / scale))))

    # Collapse repeats so the output MIDI does not carry redundant tempo events.
    collapsed: list[tuple[int, int]] = []
    for tick, tempo in changes:
        if collapsed and collapsed[-1][1] == tempo:
            continue
        collapsed.append((tick, tempo))
    return TempoMap(score.ticks_per_beat, collapsed)


def count_in_ticks(project: ProjectConfig, score: Score) -> int:
    return project.render.count_in_bars * score.ticks_per_bar


def count_in_seconds(
    project: ProjectConfig, score: Score, tempo_scale: float = 1.0
) -> float:
    """Silence-plus-click before bar 1, measured at the opening tempo.

    The count-in always runs at the tempo the music starts on, so it is timed
    off tick 0 rather than walked through the tempo map.
    """
    if project.render.count_in_bars <= 0:
        return 0.0
    tempo_map = effective_tempo_map(project, score, tempo_scale)
    return mido.tick2second(
        count_in_ticks(project, score), score.ticks_per_beat, tempo_map.tempo_at(0)
    )


def click_enabled_at_bar(project: ProjectConfig, bar: int) -> bool:
    section = project.section_at_bar(bar)
    if section is not None and section.click is not None:
        return section.click
    return project.render.click_through


def running_click_ticks(
    project: ProjectConfig, score: Score
) -> list[tuple[int, bool]]:
    """(tick, is_downbeat) for every click that sounds under the take."""
    if score.ticks_per_beat <= 0:
        return []
    beats: list[tuple[int, bool]] = []
    tick = 0
    while tick < score.duration_tick:
        bar = bar_of_tick(score, tick)
        if click_enabled_at_bar(project, bar):
            beats.append((tick, tick % score.ticks_per_bar == 0))
        tick += score.ticks_per_beat
    return beats


def section_spans(
    project: ProjectConfig, score: Score, tempo_scale: float = 1.0
) -> list[SectionSpan]:
    if not project.sections:
        return []
    tempo_map = effective_tempo_map(project, score, tempo_scale)
    offset = count_in_seconds(project, score, tempo_scale)
    total_bars = max(1, len(score.bar_ticks()) - 1)

    spans: list[SectionSpan] = []
    for section in project.sections:
        if section.from_bar > total_bars:
            raise ValueError(
                f"section {section.name!r} starts at bar {section.from_bar} but the "
                f"score only has {total_bars} bars"
            )
        start_tick = bar_tick(score, section.from_bar)
        end_tick = min(bar_tick(score, section.to_bar + 1), score.duration_tick)
        spans.append(
            SectionSpan(
                name=section.name,
                from_bar=section.from_bar,
                to_bar=min(section.to_bar, total_bars),
                start_tick=start_tick,
                end_tick=end_tick,
                start_s=offset + tempo_map.tick_to_second(start_tick),
                end_s=offset + tempo_map.tick_to_second(end_tick),
                tempo_scale=section.tempo_scale,
                click=click_enabled_at_bar(project, section.from_bar),
                rubato=section.rubato,
            )
        )
    return spans


def marker_points(
    project: ProjectConfig, score: Score, tempo_scale: float = 1.0
) -> list[MarkerPoint]:
    if not project.markers:
        return []
    tempo_map = effective_tempo_map(project, score, tempo_scale)
    offset = count_in_seconds(project, score, tempo_scale)
    total_bars = max(1, len(score.bar_ticks()) - 1)

    points: list[MarkerPoint] = []
    for marker in project.markers:
        if marker.bar > total_bars:
            raise ValueError(
                f"marker {marker.label!r} is at bar {marker.bar} but the score "
                f"only has {total_bars} bars"
            )
        tick = bar_tick(score, marker.bar)
        points.append(
            MarkerPoint(
                bar=marker.bar,
                label=marker.label,
                tick=tick,
                at_s=offset + tempo_map.tick_to_second(tick),
                rgb=marker.rgb,
            )
        )
    return points
