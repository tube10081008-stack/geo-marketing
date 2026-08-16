"""Read note values from the engraving itself: stems, beams, ties.

An earlier pass guessed durations from the notehead's fill, which cannot tell a
quarter from an eighth and produced a part that plodded in even notes. The
information was in the file the whole time — a Finale PDF draws every stem as a
vertical rule and every beam as a filled quadrilateral, so a note's value can be
counted rather than assumed:

    open head, no stem              whole
    open head + stem                half
    filled head + stem, no beam     quarter
    filled head + stem + 1 beam     eighth
    filled head + stem + 2 beams    sixteenth
    ... times 1.5 for each dot

Ties are thin filled curves joining two noteheads of the same pitch; the second
note is absorbed into the first rather than struck again.
"""

from __future__ import annotations

from dataclasses import dataclass

from .scorepdf import NoteGlyph, Staff

STEM_MAX_DX = 1.6          # points between a notehead edge and its stem
STEM_MIN_HEIGHT = 8.0
BEAM_MIN_THICKNESS = 2.5   # a beam is solid; a tie is a hairline
BEAM_MAX_POINTS = 6        # beams are quadrilaterals, slurs are polylines
TIE_MAX_THICKNESS = 3.0
TIE_MIN_POINTS = 7
EIGHTHS = {"whole": 8.0, "half": 4.0, "quarter": 2.0, "eighth": 1.0, "sixteenth": 0.5}


@dataclass
class Stem:
    x: float
    y0: float
    y1: float


@dataclass
class Beam:
    x0: float
    x1: float
    y: float


@dataclass
class Tie:
    x0: float
    x1: float
    y: float


def find_stems(page, staff: Staff) -> list[Stem]:
    stems: list[Stem] = []
    for line in page.lines:
        if abs(line["x0"] - line["x1"]) > 0.8:
            continue
        low, high = min(line["y0"], line["y1"]), max(line["y0"], line["y1"])
        if high - low < STEM_MIN_HEIGHT:
            continue
        if not staff.contains((low + high) / 2, ledger=10):
            continue
        # A barline spans the whole staff and then some; a stem does not reach
        # both outer lines.
        if low <= staff.lines[0] + 1.0 and high >= staff.lines[-1] - 1.0:
            continue
        stems.append(Stem(x=float(line["x0"]), y0=low, y1=high))
    return sorted(stems, key=lambda s: s.x)


def find_beams(page, staff: Staff) -> list[Beam]:
    beams: list[Beam] = []
    for curve in page.curves:
        if not curve.get("fill"):
            continue
        if len(curve.get("pts", [])) > BEAM_MAX_POINTS:
            continue
        if curve["height"] < BEAM_MIN_THICKNESS or curve["width"] < 6:
            continue
        if not staff.contains((curve["y0"] + curve["y1"]) / 2, ledger=12):
            continue
        beams.append(
            Beam(x0=float(curve["x0"]), x1=float(curve["x1"]),
                 y=float((curve["y0"] + curve["y1"]) / 2))
        )
    return beams


def find_ties(page, staff: Staff) -> list[Tie]:
    ties: list[Tie] = []
    for curve in page.curves:
        if not curve.get("fill"):
            continue
        if len(curve.get("pts", [])) < TIE_MIN_POINTS:
            continue
        if curve["height"] > TIE_MAX_THICKNESS or curve["width"] < 6:
            continue
        if not staff.contains((curve["y0"] + curve["y1"]) / 2, ledger=12):
            continue
        ties.append(
            Tie(x0=float(curve["x0"]), x1=float(curve["x1"]),
                y=float((curve["y0"] + curve["y1"]) / 2))
        )
    return ties


def stem_for(note: NoteGlyph, stems: list[Stem], head_width: float = 5.3) -> Stem | None:
    """The rule touching this notehead, on either side (stem up or stem down)."""
    best: Stem | None = None
    best_gap = STEM_MAX_DX
    for stem in stems:
        gap = min(abs(stem.x - note.x), abs(stem.x - (note.x + head_width)))
        if gap <= best_gap:
            best, best_gap = stem, gap
    return best


def beams_on(stem: Stem, beams: list[Beam], tolerance: float = 2.0) -> int:
    """How many beam bars cross this stem — one per level of subdivision."""
    return sum(
        1
        for beam in beams
        if beam.x0 - tolerance <= stem.x <= beam.x1 + tolerance
        and min(stem.y0, stem.y1) - 3 <= beam.y <= max(stem.y0, stem.y1) + 3
    )


def note_value(
    note: NoteGlyph,
    filled: bool,
    stems: list[Stem],
    beams: list[Beam],
    dotted: bool = False,
) -> float:
    """Duration of one notehead in eighth-note units, counted from the engraving."""
    stem = stem_for(note, stems)
    if not filled:
        value = EIGHTHS["half"] if stem else EIGHTHS["whole"]
    elif stem is None:
        value = EIGHTHS["quarter"]
    else:
        count = beams_on(stem, beams)
        value = {0: EIGHTHS["quarter"], 1: EIGHTHS["eighth"]}.get(
            count, EIGHTHS["sixteenth"]
        )
    return value * (1.5 if dotted else 1.0)


def tied_forward(
    note: NoteGlyph, following: NoteGlyph | None, ties: list[Tie], staff: Staff
) -> bool:
    """True when a tie runs from this notehead into the next one, same pitch."""
    if following is None or following.pitch != note.pitch:
        return False
    note_y = staff.lines[0] + note.step * staff.half
    for tie in ties:
        if (
            note.x - 2 <= tie.x0 <= following.x + 6
            and following.x - 8 <= tie.x1 <= following.x + 12
            and abs(tie.y - note_y) <= staff.space * 2.2
        ):
            return True
    return False
