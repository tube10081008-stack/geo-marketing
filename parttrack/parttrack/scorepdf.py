"""Read pitches straight out of an engraved (vector) score PDF.

Rental scores from the major houses are typeset in Finale and distributed as
vector PDFs. That means the notes are not pixels to be guessed at — the staff
lines are real line objects and every notehead is a real glyph at an exact
coordinate. Reading them is arithmetic, not OMR, and arithmetic does not
mis-transcribe a part.

Two things make it non-obvious:

1. The music font is subset-embedded, so glyphs arrive as opaque CIDs with no
   Unicode mapping. Noteheads are identified by width and by the fact that they
   land on the staff's half-space grid.
2. Every glyph reports the same em-box height, so a glyph's bounding-box centre
   sits a fixed distance from the symbol it actually draws. That offset is
   recovered from the key signature, whose staff positions are known a priori —
   three independent accidentals must agree, or calibration fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LETTERS = "CDEFGAB"
SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Staff position (half-steps above the bottom line) of each accidental in a
# treble-clef key signature, in the order engravers write them.
FLAT_ORDER = ["B", "E", "A", "D", "G", "C", "F"]
TREBLE_FLAT_STEPS = {"B": 4, "E": 7, "A": 3, "D": 6, "G": 2, "C": 5, "F": 1}
SHARP_ORDER = ["F", "C", "G", "D", "A", "E", "B"]
TREBLE_SHARP_STEPS = {"F": 8, "C": 5, "G": 9, "D": 6, "A": 3, "E": 7, "B": 4}

# Notehead width band at the usual 16.3pt staff size. Time-signature digits and
# rests overlap the loose end of this range, so width alone cannot identify a
# notehead — it only narrows the field for `detect_notehead_cids`.
NOTEHEAD_MIN_WIDTH = 5.0
NOTEHEAD_MAX_WIDTH = 5.6
GRID_TOLERANCE = 0.2  # half-steps
CALIBRATION_TOLERANCE = 0.15


class CalibrationError(RuntimeError):
    """Raised when the key signature does not pin down the glyph offset."""


@dataclass
class Staff:
    lines: list[float]  # five y positions, lowest first
    space: float
    x0: float
    x1: float
    lyrics: str = ""
    is_vocal: bool = False

    @property
    def half(self) -> float:
        return self.space / 2

    def contains(self, y: float, ledger: int = 8) -> bool:
        return self.lines[0] - self.half * ledger < y < self.lines[-1] + self.half * ledger


@dataclass
class NoteGlyph:
    x: float
    step: int          # half-steps above the bottom staff line
    pitch: int         # MIDI note number, key signature applied
    name: str
    filled: bool       # solid notehead (quarter or shorter)


def glyph_cid(char: dict) -> int:
    text = char["text"]
    return int(text[5:-1]) if text.startswith("(cid:") else ord(text)


def find_staves(page, min_width: float = 100.0) -> list[Staff]:
    """Group horizontal rules into five-line staves, top of page first."""
    horizontals = [
        line
        for line in page.lines
        if abs(line["y0"] - line["y1"]) < 0.5 and (line["x1"] - line["x0"]) > min_width
    ]
    ys = sorted({round(line["y0"], 1) for line in horizontals})

    staves: list[Staff] = []
    index = 0
    while index < len(ys) - 4:
        window = ys[index : index + 5]
        gaps = np.diff(window)
        if gaps.std() < 0.35 and 2.5 < gaps.mean() < 6.0:
            on_bottom = [l for l in horizontals if abs(l["y0"] - window[0]) < 0.3]
            staves.append(
                Staff(
                    lines=[float(v) for v in window],
                    space=float(gaps.mean()),
                    x0=min(l["x0"] for l in on_bottom),
                    x1=max(l["x1"] for l in on_bottom),
                )
            )
            index += 5
        else:
            index += 1

    staves.sort(key=lambda s: -s.lines[0])
    return staves


def label_vocal_staves(page, staves: list[Staff]) -> list[Staff]:
    """A staff with running text just beneath it is carrying a lyric line."""
    words = [
        char
        for char in page.chars
        if "Maestro" not in char["fontname"] and char["text"].strip()
    ]
    for staff in staves:
        below = [
            char
            for char in words
            if staff.lines[0] - 26 < (char["y0"] + char["y1"]) / 2 < staff.lines[0] - 2
            and staff.x0 - 5 < char["x0"] < staff.x1
        ]
        staff.lyrics = "".join(c["text"] for c in sorted(below, key=lambda c: c["x0"]))
        # Chord symbols sit under piano staves and look superficially like text
        # ("Gm7", "Ab/Bb", "Eadd9", "Cadd9"), so neither a character count nor a
        # lower-case count separates them — a row of chord symbols carries
        # plenty of 'm', 'a', 'd'. What it does not carry is variety: sung text
        # uses most of the alphabet, chord suffixes use a handful of letters.
        distinct = {ch for ch in staff.lyrics if ch.islower() and ch.isascii()}
        staff.is_vocal = len(below) > 6 and len(distinct) >= 6
    return staves


def _raw_step(char: dict, staff: Staff) -> float:
    centre = (char["y0"] + char["y1"]) / 2
    return (centre - staff.lines[0]) / staff.half


def calibrate(page, staff: Staff, key: str) -> float:
    """Recover the em-box offset from the key signature.

    `key` is like "Eb" or "F" or "D". The accidentals in a key signature sit at
    staff positions fixed by convention, so comparing where they are drawn with
    where they belong gives the offset every other glyph shares.
    """
    accidentals, order, table = _key_signature(key)
    if not accidentals:
        raise CalibrationError(
            f"key {key!r} has no accidentals to calibrate against; "
            "pass a key with at least one sharp or flat"
        )

    glyphs = [
        char
        for char in page.chars
        if "Maestro" in char["fontname"]
        and staff.contains((char["y0"] + char["y1"]) / 2)
        and staff.x0 - 2 < char["x0"] < staff.x0 + 60
    ]
    # The accidentals are the run of identical narrow glyphs after the clef.
    by_cid: dict[int, list[dict]] = {}
    for char in glyphs:
        by_cid.setdefault(glyph_cid(char), []).append(char)
    candidates = [
        sorted(items, key=lambda c: c["x0"])
        for items in by_cid.values()
        if len(items) >= len(accidentals)
    ]
    if not candidates:
        raise CalibrationError("could not find the key signature accidentals")

    for run in candidates:
        offsets = []
        for char, letter in zip(run[: len(accidentals)], accidentals):
            offsets.append(table[letter] - _raw_step(char, staff))
        spread = max(offsets) - min(offsets)
        if spread < CALIBRATION_TOLERANCE:
            return float(np.mean(offsets))

    raise CalibrationError(
        "key signature glyphs did not agree on a single offset; the staff or "
        "key is probably wrong"
    )


def _key_signature(key: str) -> tuple[list[str], list[str], dict[str, int]]:
    flats = {"F": 1, "Bb": 2, "Eb": 3, "Ab": 4, "Db": 5, "Gb": 6}
    sharps = {"G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6}
    if key in flats:
        return FLAT_ORDER[: flats[key]], FLAT_ORDER, TREBLE_FLAT_STEPS
    if key in sharps:
        return SHARP_ORDER[: sharps[key]], SHARP_ORDER, TREBLE_SHARP_STEPS
    return [], FLAT_ORDER, TREBLE_FLAT_STEPS


FLAT_KEYS = ["C", "F", "Bb", "Eb", "Ab", "Db", "Gb"]
SHARP_KEYS = ["C", "G", "D", "A", "E", "B", "F#"]


def detect_key(page, staff: Staff, accidental_width: float = 3.6) -> str:
    """Read the key off the signature itself, so a modulation cannot be missed.

    The accidentals are the run of identical narrow glyphs sitting between the
    clef and the first note. Counting them gives the key; whether they climb or
    fall across the run tells flats from sharps (flats descend by a fourth then
    rise by a fifth, sharps do the reverse), but the reliable discriminator here
    is simply that a flat sits lower than the note it alters.
    """
    head = [
        char
        for char in page.chars
        if "Maestro" in char["fontname"]
        and staff.contains((char["y0"] + char["y1"]) / 2)
        and staff.x0 - 2 < char["x0"] < staff.x0 + 60
        and (char["x1"] - char["x0"]) < accidental_width
    ]
    if not head:
        return "C"

    by_cid: dict[int, list[dict]] = {}
    for char in head:
        by_cid.setdefault(glyph_cid(char), []).append(char)
    cid, run = max(by_cid.items(), key=lambda kv: len(kv[1]))
    run.sort(key=lambda c: c["x0"])
    count = min(len(run), 6)

    # Try the flat reading first: the flats' known positions must line up on a
    # single offset. If they do not, read it as sharps.
    for keys, table, order in (
        (FLAT_KEYS, TREBLE_FLAT_STEPS, FLAT_ORDER),
        (SHARP_KEYS, TREBLE_SHARP_STEPS, SHARP_ORDER),
    ):
        offsets = [
            table[letter] - _raw_step(char, staff)
            for char, letter in zip(run[:count], order[:count])
        ]
        if max(offsets) - min(offsets) < CALIBRATION_TOLERANCE:
            return keys[count]
    return "C"


def detect_notehead_cids(page, staves: list[Staff], limit: int = 2) -> set[int]:
    """Work out which CIDs draw noteheads, by how they behave on the page.

    Width narrows the field but does not settle it — time-signature digits are
    the same size. What separates a notehead is that it appears constantly, all
    across the staff rather than bunched at the left where clefs and meters
    live, and always on the half-space grid.
    """
    scores: dict[int, dict] = {}
    for staff in staves:
        span = max(1.0, staff.x1 - staff.x0)
        for char in page.chars:
            if "Maestro" not in char["fontname"]:
                continue
            width = char["x1"] - char["x0"]
            if not NOTEHEAD_MIN_WIDTH <= width <= NOTEHEAD_MAX_WIDTH:
                continue
            centre = (char["y0"] + char["y1"]) / 2
            if not staff.contains(centre):
                continue
            entry = scores.setdefault(glyph_cid(char), {"count": 0, "spread": []})
            entry["count"] += 1
            entry["spread"].append((char["x0"] - staff.x0) / span)

    ranked = []
    for cid, entry in scores.items():
        if entry["count"] < 4:
            continue
        # Clefs, key signatures and meters cluster in the first fifth of the
        # staff; noteheads are strewn across all of it.
        beyond_head = sum(1 for value in entry["spread"] if value > 0.2) / entry["count"]
        ranked.append((beyond_head * entry["count"], cid))

    ranked.sort(reverse=True)
    return {cid for _, cid in ranked[:limit]}


def step_to_pitch(step: int, clef: str = "treble") -> tuple[int, str]:
    """Half-steps above the bottom line -> (MIDI pitch, letter name)."""
    base_letter, base_octave = ("E", 4) if clef == "treble" else ("G", 2)
    index = LETTERS.index(base_letter) + step
    letter = LETTERS[index % 7]
    octave = base_octave + index // 7
    return (octave + 1) * 12 + SEMITONE[letter], letter


def extract_notes(
    page,
    staff: Staff,
    offset: float,
    key: str = "Eb",
    clef: str = "treble",
    octave_shift: int = 0,
    notehead_cids: set[int] | None = None,
) -> list[NoteGlyph]:
    """Every notehead on one staff, left to right.

    `octave_shift` handles the octave-down treble clef used for tenor lines.
    """
    accidentals, _, _ = _key_signature(key)
    altered = set(accidentals)
    is_flat_key = key in {"F", "Bb", "Eb", "Ab", "Db", "Gb"}

    notes: list[NoteGlyph] = []
    for char in page.chars:
        if "Maestro" not in char["fontname"]:
            continue
        width = char["x1"] - char["x0"]
        if notehead_cids is not None:
            if glyph_cid(char) not in notehead_cids:
                continue
        elif not NOTEHEAD_MIN_WIDTH <= width <= NOTEHEAD_MAX_WIDTH:
            continue
        centre = (char["y0"] + char["y1"]) / 2
        if not staff.contains(centre):
            continue
        step = _raw_step(char, staff) + offset
        if abs(step - round(step)) > GRID_TOLERANCE:
            continue
        step = int(round(step))

        pitch, letter = step_to_pitch(step, clef)
        if letter in altered:
            pitch += -1 if is_flat_key else 1
        pitch += 12 * octave_shift

        name = f"{letter}{pitch // 12 - 1}"
        if letter in altered:
            name = f"{letter}{'♭' if is_flat_key else '♯'}{pitch // 12 - 1}"

        notes.append(
            NoteGlyph(
                x=float(char["x0"]),
                step=step,
                pitch=pitch,
                name=name,
                filled=width < 5.3,
            )
        )

    notes.sort(key=lambda n: n.x)
    return notes


def clef_glyph(page, staff: Staff) -> int | None:
    """CID of the clef: the leftmost music glyph sitting on the staff."""
    candidates = [
        char
        for char in page.chars
        if "Maestro" in char["fontname"]
        and staff.x0 - 6 < char["x0"] < staff.x0 + 16
        and staff.contains((char["y0"] + char["y1"]) / 2, ledger=4)
    ]
    if not candidates:
        return None
    return glyph_cid(min(candidates, key=lambda c: c["x0"]))


def clef_census(page, staves: list[Staff]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for staff in staves:
        cid = clef_glyph(page, staff)
        if cid is not None:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def detect_octave_shift(page, staff: Staff, plain_treble_cid: int | None = None) -> int:
    """Spot the octave-down treble clef used for tenor lines.

    Choral scores write tenors on a treble staff with a small 8 hung under it.
    The 8 is not a separate character — Finale ships the whole thing as its own
    clef glyph — so it is identified by having a different CID from the ordinary
    treble clef that the rest of the page uses. Missing it puts the part an
    octave too high, which is the difference between a line a tenor can sing and
    one they cannot.
    """
    cid = clef_glyph(page, staff)
    if cid is None or plain_treble_cid is None:
        return 0
    return -1 if cid != plain_treble_cid else 0


def split_voices(
    notes: list[NoteGlyph], x_tolerance: float = 2.0
) -> list[list[NoteGlyph]]:
    """Separate a staff carrying two parts into an upper and a lower line.

    Choral writing puts two voices on one staff as stacked noteheads sharing a
    stem, so soprano and alto (or tenor and bass) arrive interleaved. Notes that
    sit at the same horizontal position are one chord; the highest belongs to
    the upper part, the lowest to the lower one. Where only a single note is
    written, both parts are in unison and it belongs to both.
    """
    if not notes:
        return [[], []]

    chords: list[list[NoteGlyph]] = []
    current: list[NoteGlyph] = [notes[0]]
    for note in notes[1:]:
        if abs(note.x - current[0].x) <= x_tolerance:
            current.append(note)
        else:
            chords.append(current)
            current = [note]
    chords.append(current)

    upper: list[NoteGlyph] = []
    lower: list[NoteGlyph] = []
    for chord in chords:
        chord.sort(key=lambda n: n.pitch)
        upper.append(chord[-1])
        lower.append(chord[0])
    return [upper, lower]


def read_staff(
    page,
    staff: Staff,
    key: str,
    clef: str = "treble",
    octave_shift: int = 0,
    notehead_cids: set[int] | None = None,
):
    """Calibrate against this staff's key signature, then read its notes."""
    offset = calibrate(page, staff, key)
    return offset, extract_notes(
        page, staff, offset, key, clef, octave_shift, notehead_cids
    )
