"""Recover rhythm from an engraved score's horizontal spacing.

Engravers lay a bar out left to right in time order, so a notehead's horizontal
position inside its bar is a usable proxy for when it sounds. That is not exact
— accidentals, lyrics and ledger lines all steal width — but once the position
is quantised onto the bar's own subdivision grid, the small errors disappear and
what is left is the written rhythm.

Bars are delimited by the vertical rules that cross a staff, and numbered by the
small digits engraved above them, so a part can be reassembled across pages
without assuming every system holds the same number of bars.
"""

from __future__ import annotations

from dataclasses import dataclass

from .scorepdf import NoteGlyph, Staff

# Candidate subdivisions per bar, tried in order. 6/8 and 12/8 dominate this
# repertoire; 4/4 and 3/4 cover the rest.
MIN_BARLINE_COVERAGE = 0.9


@dataclass
class TimedNote:
    bar: int            # absolute bar number from the engraved numbering
    onset: float        # position within the bar, in grid units
    duration: float     # grid units
    pitch: int
    name: str


def find_barlines(page, staff: Staff) -> list[float]:
    """x positions of the rules that cross this staff top to bottom."""
    span = staff.lines[-1] - staff.lines[0]
    xs = []
    for line in page.lines:
        if abs(line["x0"] - line["x1"]) > 0.6:
            continue
        low, high = min(line["y0"], line["y1"]), max(line["y0"], line["y1"])
        if high - low < span * MIN_BARLINE_COVERAGE:
            continue
        if low <= staff.lines[0] + 1.5 and high >= staff.lines[-1] - 1.5:
            xs.append(round(line["x0"], 1))
    return sorted(set(xs))


def read_bar_numbers(page, staff: Staff) -> list[tuple[float, int]]:
    """(x, number) for the measure numbers engraved above this staff."""
    digits = [
        char
        for char in page.chars
        if "Maestro" not in char["fontname"]
        and char["text"].isdigit()
        and staff.lines[-1] < (char["y0"] + char["y1"]) / 2 < staff.lines[-1] + 15
    ]
    digits.sort(key=lambda c: c["x0"])

    numbers: list[tuple[float, int]] = []
    current = ""
    start = 0.0
    previous_x1 = None
    for char in digits:
        if previous_x1 is not None and char["x0"] - previous_x1 > 3.0:
            if current:
                numbers.append((start, int(current)))
            current, start = "", char["x0"]
        if not current:
            start = char["x0"]
        current += char["text"]
        previous_x1 = char["x1"]
    if current:
        numbers.append((start, int(current)))
    return numbers


def system_bar_numbers(page, staves: list[Staff]) -> dict[int, list[tuple[float, int]]]:
    """Measure numbers for every staff, borrowed from its system's top staff.

    Engravers print a measure number once per system, above the topmost staff.
    Every other staff in that system — the tenor line, the piano — carries none.
    Staves in one system share barline positions, so the numbers can be handed
    down by matching those positions. Without this two thirds of the bars have
    no number, and any positional fallback stacks bars from different pages on
    top of one another, which sounds exactly as bad as it is.
    """
    out: dict[int, list[tuple[float, int]]] = {}
    current: list[tuple[float, int]] = []
    for index, staff in enumerate(staves):
        own = enforce_monotonic(read_bar_numbers(page, staff))
        # The engraving convention itself marks the system boundaries: a measure
        # number is printed above the top staff of a system and nowhere else, so
        # a staff that has its own numbers *is* the start of a system. Every
        # staff below it belongs to that system until the next numbered staff.
        # Geometry cannot do this reliably — systems on one page span the same
        # width and break their bars in similar places.
        if own:
            current = own
        out[index] = current
    return out


def enforce_monotonic(numbers: list[tuple[float, int]]) -> list[tuple[float, int]]:
    """Drop stray digits — real measure numbers only ever increase."""
    kept: list[tuple[float, int]] = []
    for x, value in numbers:
        if kept and value <= kept[-1][1]:
            continue
        kept.append((x, value))
    return kept


# Duration in eighth-note units, read off the notation rather than the spacing.
# Horizontal position says which note comes first; it does not say how long a
# note lasts, because engraved spacing is not linear in time — beamed groups are
# packed tight while held notes are given room. The notehead itself does say.
OPEN_NOTE_EIGHTHS = 4.0     # half note
FILLED_NOTE_EIGHTHS = 1.0   # eighth note: the default in this 6/8 repertoire
DOT_FACTOR = 1.5
DOT_MAX_DISTANCE = 9.0      # points to the right of the notehead
DOT_MAX_STEP_ERROR = 1.1    # half-steps; a dot sits in the space beside the head


def dot_cid(page, staff: Staff, notehead_cids: set[int]) -> int | None:
    """The augmentation dot: the narrowest music glyph that sits on the grid."""
    widths: dict[int, list[float]] = {}
    for char in page.chars:
        if "Maestro" not in char["fontname"]:
            continue
        if not staff.contains((char["y0"] + char["y1"]) / 2):
            continue
        cid = int(char["text"][5:-1]) if char["text"].startswith("(cid:") else ord(char["text"])
        if cid in notehead_cids:
            continue
        widths.setdefault(cid, []).append(char["x1"] - char["x0"])

    narrow = [
        (sum(values) / len(values), cid, len(values))
        for cid, values in widths.items()
        if sum(values) / len(values) < 3.2 and len(values) >= 3
    ]
    if not narrow:
        return None
    narrow.sort(key=lambda item: (-item[2], item[0]))
    return narrow[0][1]


def note_duration(
    note: NoteGlyph,
    filled: bool,
    dots: list[tuple[float, float]],
    staff: Staff,
    offset: float = 0.0,
) -> float:
    """Eighth-note units for one notehead, applying an augmentation dot.

    Dots arrive as raw glyph centres, so they need the same em-box correction as
    the noteheads before the two can be compared — otherwise every dot misses
    its note and the whole part comes out in even eighths.
    """
    base = FILLED_NOTE_EIGHTHS if filled else OPEN_NOTE_EIGHTHS
    for dot_x, dot_raw_y in dots:
        dot_step = (dot_raw_y - staff.lines[0]) / staff.half + offset
        if 0 < dot_x - note.x <= DOT_MAX_DISTANCE and abs(dot_step - note.step) <= (
            DOT_MAX_STEP_ERROR
        ):
            return base * DOT_FACTOR
    return base


def time_staff(
    page,
    staff: Staff,
    notes: list[NoteGlyph],
    default_bar: int = 1,
    beats_per_bar: float = 6.0,
    dots: list[tuple[float, float]] | None = None,
    offset: float = 0.0,
    numbers: list[tuple[float, int]] | None = None,
) -> list[TimedNote]:
    """Place a staff's notes on (bar, beat) using barlines and spacing."""
    if not notes:
        return []
    dots = dots or []

    barlines = find_barlines(page, staff)
    if numbers is None:
        numbers = enforce_monotonic(read_bar_numbers(page, staff))
    if len(barlines) < 2:
        barlines = [staff.x0, staff.x1]

    # Bar spans between consecutive rules, ignoring the bracket at the very left.
    spans: list[tuple[float, float]] = []
    for left, right in zip(barlines, barlines[1:]):
        if right - left < 12:      # system bracket or repeat pair, not a bar
            continue
        spans.append((left, right))
    if not spans:
        spans = [(staff.x0, staff.x1)]

    def bar_number(index: int, left: float, right: float) -> int:
        """Numbered bars win; unnumbered ones count on from the last known bar.

        A staff with no numbers anywhere cannot be placed in the piece at all —
        guessing puts its bars on top of another page's, so it is skipped.
        """
        for x, value in numbers:
            if left - 6 <= x < right:
                return value
        preceding = [value for x, value in numbers if x < left]
        if preceding:
            offset_from = max(preceding)
            gap = sum(1 for a, b in spans[:index] if a >= max(
                x for x, v in numbers if v == offset_from))
            return offset_from + max(1, gap)
        return -1

    timed: list[TimedNote] = []
    for index, (left, right) in enumerate(spans):
        inside = [n for n in notes if left <= n.x < right]
        if not inside:
            continue

        durations = [
            note_duration(note, note.filled, dots, staff, offset) for note in inside
        ]
        # Notes follow one another in written order; the durations decide where
        # each one starts. If the bar does not add up to its meter the whole bar
        # is scaled, which keeps the rhythm's proportions even when a rest or a
        # tie went unread.
        total = sum(durations)
        if beats_per_bar and total > 0:
            scale = beats_per_bar / total
            if 0.34 < scale < 3.0:
                durations = [d * scale for d in durations]

        number = bar_number(index, left, right)
        if number < 0:
            continue
        cursor = 0.0
        for note, duration in zip(inside, durations):
            timed.append(
                TimedNote(
                    bar=number,
                    onset=cursor,
                    duration=duration,
                    pitch=note.pitch,
                    name=note.name,
                )
            )
            cursor += duration
    return timed


def merge_repeats(notes: list[TimedNote]) -> list[TimedNote]:
    """Collapse a pitch restated at the same instant (unison across voices)."""
    seen: set[tuple[int, float, int]] = set()
    out: list[TimedNote] = []
    for note in sorted(notes, key=lambda n: (n.bar, n.onset, n.pitch)):
        key = (note.bar, note.onset, note.pitch)
        if key in seen:
            continue
        seen.add(key)
        out.append(note)
    return out
