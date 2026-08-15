"""
The report-facing pattern system, and the aggregation that turns a flight's
annotations into the patterns page 1 talks about.

Why this module exists
----------------------
V1 labelled all fifteen IPM Farm zones "Nutrient / Vigor" and then printed all
fifteen of them, three times over. The causes the agronomist actually wrote
down were more varied than that single label — overmounding, water stress at a
drip line end, weed pressure — so the report was both repetitive and less
accurate than the data underneath it.

V2 fixes that at the source rather than in the layout: the categories the
agronomist picks from ARE the patterns the report groups by, so what is on the
review screen and what is on page 1 can never disagree. `classify()` suggests
one from the agronomist's own text at import time; the agronomist can change it
in one click on the review page, and the report follows.

The guardrail
-------------
Everything this module writes into the report is assembled from the
agronomist's own observation, likely-cause and recommendation text. It does not
introduce a cause, a diagnosis or a recommendation that is not already in the
source annotation — it only counts, sums, de-duplicates and joins. That is a
property of the code, not a prompt, so it holds for every flight.

The colour system
-----------------
Separate from the internal severity colour code the agronomists use while
annotating in DroneDeploy (blue/green/yellow/red, by urgency), which stays as
it is for internal triage. The report-facing map colours by category, because
what a farmer needs to read spatially is not "how urgent" but "what kind, and
where". No colour here means "healthy" or "fine": every colour on the map marks
something the agronomist flagged.
"""
import math
import re
from collections import Counter

# ---------------------------------------------------------------- the patterns
# Ordered as the report prefers to introduce them when acreage ties. `soft` and
# `line` are the tint and hairline used for the group's table header and card.
PATTERNS = [
    {
        "key": "irrigation",
        "label": "Irrigation / Moisture",
        "colour": "#2E6DB4", "soft": "#E8F0F9", "line": "#BBD2EA",
        "note": "Water availability or distribution",
    },
    {
        "key": "soil",
        "label": "Soil Fertility / Nutrition",
        "colour": "#D9A227", "soft": "#FBF3DF", "line": "#EDD9A4",
        "note": "Soil condition or nutrient availability",
    },
    {
        "key": "establishment",
        "label": "Crop Establishment",
        "colour": "#B4552E", "soft": "#F8ECE6", "line": "#E5C4B4",
        "note": "Germination, emergence, spacing or mounding",
    },
    {
        "key": "pest",
        "label": "Pest / Disease",
        "colour": "#8E2420", "soft": "#F6E7E6", "line": "#DFBAB8",
        "note": "Suspected pest pressure or disease",
    },
    {
        "key": "weeds",
        "label": "Weeds",
        "colour": "#5F6F4E", "soft": "#EDF0E8", "line": "#C9D2BF",
        "note": "Weed pressure",
    },
    {
        "key": "unclear",
        "label": "Needs Investigation",
        "colour": "#6A3D77", "soft": "#F1EAF3", "line": "#D4BFDA",
        "note": "Flagged, cause not yet established",
    },
]

BY_KEY = {p["key"]: p for p in PATTERNS}
BY_LABEL = {p["label"]: p for p in PATTERNS}

# What the review-page dropdown offers, in the order it offers it.
CATEGORIES = [p["label"] for p in PATTERNS]

# The label used when a finding carries a category this release does not know.
FALLBACK = BY_KEY["unclear"]

# label -> colour, for the interface. The review page, the dashboard chart and
# the report all read this one table, so a category is the same colour whether
# an agronomist is filing it or a farmer is reading it.
COLOURS = {p["label"]: p["colour"] for p in PATTERNS}


def spec(category):
    """The pattern record for a stored category label. Never raises."""
    return BY_LABEL.get((category or "").strip(), FALLBACK)


# ---------------------------------------------------------------- classifying
# Terms are matched against the agronomist's own words. Weights say how
# diagnostic a term is, not how serious it is: "drip" names one system and
# almost nothing else, so it counts for more than "water", which turns up in
# half the recommendations on any farm.
#
# Patterns are read as regular expressions so a term can be anchored where a
# bare substring would misfire — \brot\b rather than "rot", which would
# otherwise match "rotation".
TERMS = {
    "irrigation": [
        (r"irrigat", 3), (r"\bdrip", 3), (r"\bmoist", 3), (r"waterlog", 3),
        (r"drought", 3), (r"\bwater", 2), (r"\bdry\b", 1), (r"pressure", 1),
        (r"\bdrain", 2), (r"\bflood", 2),
    ],
    "soil": [
        (r"fertil", 3), (r"\bnutri", 3), (r"defici", 3), (r"\bnpk\b", 3),
        (r"nitrogen", 3), (r"potassium", 3), (r"phosph", 3), (r"manure", 3),
        (r"compost", 3), (r"\bsoil", 2), (r"\buptake", 1), (r"\bph\b", 1),
        (r"acidic", 2), (r"alkalin", 2), (r"salin", 2),
    ],
    "establishment": [
        (r"mound", 3), (r"germinat", 3), (r"emergen", 3), (r"transplant", 3),
        (r"\bsowing", 3), (r"\bsown", 3), (r"\bcover", 2), (r"\bridge", 2),
        (r"\bfurrow", 2), (r"spacing", 2), (r"\bseed", 2), (r"\bgap", 2),
        (r"population", 2), (r"\bplanting", 2),
    ],
    "pest": [
        (r"\bpest", 3), (r"disease", 3), (r"blight", 3), (r"fung", 3),
        (r"mildew", 3), (r"aphid", 3), (r"insect", 3), (r"larva", 3),
        (r"caterpillar", 3), (r"nematode", 3), (r"\bvirus", 3), (r"borer", 3),
        (r"thrip", 3), (r"whitefl", 3), (r"\brust\b", 3), (r"canker", 3),
        (r"\bmite", 2), (r"\bworm", 2), (r"lesion", 2), (r"\brot\b", 2),
        (r"\brotting\b", 2), (r"\bwilt", 1),
    ],
    "weeds": [
        (r"\bweed", 3), (r"striga", 3), (r"couch grass", 3),
    ],
}

# The likely cause is what the agronomist concluded, so it carries most of the
# signal; the recommendation is what they decided to do about it; the
# observation is the symptom, which is the least specific of the three.
FIELD_WEIGHTS = (("likely_cause", 3), ("recommendation", 2), ("observation", 1))

# Compiled once. Order within a pattern does not matter — every term that hits
# adds its weight, and each term counts at most once per field.
_COMPILED = {
    key: [(re.compile(rx, re.I), w) for rx, w in terms]
    for key, terms in TERMS.items()
}


def score_text(text):
    """Weighted hit count per pattern for one piece of text."""
    scores = {}
    text = (text or "").strip()
    if not text:
        return scores
    for key, terms in _COMPILED.items():
        total = sum(weight for rx, weight in terms if rx.search(text))
        if total:
            scores[key] = total
    return scores


def classify(observation="", likely_cause="", recommendation="", default=None):
    """
    Suggest a category label from the agronomist's own words.

    Returns the label, so it can be written straight into Finding.category and
    shown in the review dropdown. When nothing in the text points anywhere —
    "Poor plant vigour" with no cause written yet — it returns Needs
    Investigation rather than guessing, which is the honest answer and is what
    the purple pin on the map means.
    """
    fields = {
        "observation": observation,
        "likely_cause": likely_cause,
        "recommendation": recommendation,
    }
    totals = Counter()
    for field, weight in FIELD_WEIGHTS:
        for key, score in score_text(fields.get(field)).items():
            totals[key] += score * weight

    if not totals:
        return default or FALLBACK["label"]

    best = max(totals.values())
    winners = [p["key"] for p in PATTERNS if totals.get(p["key"]) == best]
    return BY_KEY[winners[0]]["label"]          # PATTERNS order breaks the tie


# ---------------------------------------------------------------- phrasing
# Splitting the agronomist's text into atoms is what lets the report say
# "associated with soil condition and soil fertility" from four separate
# annotations without inventing a word. Atoms are split on the punctuation
# people actually type between two clauses, including the full stop, because
# "Reduce soil cover and maintain proper mound height. Oversee the process..."
# is three instructions in one cell.
_ATOM_SPLIT = re.compile(r"[;,/]|\.\s+| \u2014 | \u2013 | - | and (?=[a-z])", re.I)
_TIDY = re.compile(r"\s+")

# A clause longer than this is prose, not a label. Prose reads badly inside a
# joined sentence and is usually the tail of a recommendation rather than the
# recommendation itself, so it is left for the detail table on page 3 to carry
# in full.
MAX_PHRASE_CHARS = 48

# Words kept exactly as typed when a phrase is normalised for display.
_KEEP_CASE = {"ph"}


def atoms(text):
    """The distinct clauses inside one free-text field, tidied but not reworded."""
    out = []
    for piece in _ATOM_SPLIT.split(text or ""):
        piece = _TIDY.sub(" ", piece).strip(" .;,/-\u2013\u2014")
        if 2 < len(piece) <= MAX_PHRASE_CHARS:
            out.append(piece)
    return out


def _key(phrase):
    """Case- and punctuation-insensitive identity, so 'Soil fertility' == 'soil fertility'."""
    return _TIDY.sub(" ", re.sub(r"[^a-z0-9 ]", " ", phrase.lower())).strip()


def normalise_case(phrase):
    """
    Lower-case the agronomist's Title Case so a joined sentence reads as prose.

    Acronyms (NPK) and known mixed-case terms (pH) are left alone. This changes
    presentation only — no word is added, removed or replaced.
    """
    words = []
    for word in phrase.split(" "):
        if word.isupper() and len(word) > 1:
            words.append(word)
        elif word.lower() in _KEEP_CASE:
            words.append(word)
        else:
            words.append(word.lower())
    return " ".join(words)


def _drop_subsumed(ranked):
    """
    Remove a clause that merely spells out a shorter one already chosen.

    "Soil testing" and "Soil testing to identify nutrient deficiencies" are the
    same recommendation written twice; printing both reads as padding. The more
    frequent survives, and a tie goes to the shorter.
    """
    kept = []
    for k in ranked:
        if not any(k in other or other in k for other in kept):
            kept.append(k)
    return kept


def common_phrases(findings, field, limit=2, min_count=1):
    """
    The clauses this group of findings says most often, most frequent first.

    `min_count` keeps a one-off out of a sentence that speaks for the whole
    group: in a nine-area pattern, something a single annotation mentions is
    not what the group has in common, and page 3 still carries it in full.
    Ties break on the order the agronomist entered them, so the same flight
    always produces the same sentence.
    """
    counts, first_seen, display = Counter(), {}, {}
    for order, finding in enumerate(findings):
        for slot, phrase in enumerate(atoms(getattr(finding, field, "") or "")):
            k = _key(phrase)
            if not k:
                continue
            counts[k] += 1
            if k not in first_seen:
                # (which annotation, then where inside it) keeps the
                # agronomist's own running order as the tie-break, so the more
                # specific clause they wrote first is not beaten by a shorter
                # one they added after it.
                first_seen[k] = (order, slot)
                display[k] = normalise_case(phrase)

    eligible = [k for k in counts if counts[k] >= min_count] or list(counts)
    ranked = sorted(eligible, key=lambda k: (-counts[k], first_seen[k]))
    return [display[k] for k in _drop_subsumed(ranked)[:limit]]


def shared_threshold(n):
    """How often a clause must appear before it can speak for the whole group."""
    return 2 if n >= 3 else 1


def join_phrases(phrases, conjunction="and"):
    """'a', 'a and b', 'a, b and c'."""
    parts = [p for p in phrases if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" {conjunction} " + parts[-1]


def sentence_case(text):
    """Capitalise the first letter and nothing else. Leaves acronyms alone."""
    text = (text or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


_NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven",
                 "eight", "nine", "ten", "eleven", "twelve"]


def number_word(n):
    """Words up to twelve, digits after — how a person writes a count."""
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def one_dp(value):
    """Round half up to one decimal, so 19.15 acres prints as 19.2 and not 19.1."""
    return math.floor(float(value) * 10 + 0.5) / 10


def acres_text(value):
    """'~13.4'. Areas under a tenth of an acre keep two places so 0.01 is visible."""
    if not value:
        return ""
    if value < 0.1:
        return f"~{value:.2f}"
    return f"~{one_dp(value):.1f}"


# ---------------------------------------------------------------- aggregation
def _acres(finding):
    try:
        v = float(finding.area_acres)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dominant(findings, field):
    """True when one clause is said more often than any other in this field."""
    counts = Counter()
    for finding in findings:
        for phrase in atoms(getattr(finding, field, "") or ""):
            counts[_key(phrase)] += 1
    top = counts.most_common(2)
    return len(top) > 1 and top[0][1] > top[1][1]


def group_findings(findings, numbers):
    """
    Group a flight's findings by the category the agronomist assigned.

    `numbers` maps finding id -> the zone number printed on the map, so a group
    can name its zones. Groups come back ordered by acreage, largest first,
    which is the order the summary and the detail pages both read in: the
    biggest thing on the farm is the first thing the farmer sees.

    Every finding lands in exactly one group and nothing is dropped, so the
    counts on page 1 always add up to the rows on page 3.
    """
    buckets = {}
    for finding in findings:
        pattern = spec(finding.category)
        buckets.setdefault(pattern["key"], []).append(finding)

    groups = []
    for pattern in PATTERNS:
        items = buckets.get(pattern["key"])
        if not items:
            continue
        items = sorted(items, key=lambda f: numbers.get(f.id, 0))
        acres = round(sum(_acres(f) for f in items), 2)
        floor = shared_threshold(len(items))
        # A second symptom is only worth naming when no single one dominates:
        # "uneven growth or poor plant vigour" is useful, "poor plant vigour or
        # poor plant vigour" is noise.
        observations = common_phrases(items, "observation", limit=2)[:2]
        if len(observations) == 2 and _dominant(items, "observation"):
            observations = observations[:1]
        causes = common_phrases(items, "likely_cause", limit=2, min_count=floor)
        actions = common_phrases(items, "recommendation", limit=2, min_count=floor)
        groups.append({
            **pattern,
            "findings": items,
            "count": len(items),
            "acres": acres,
            "acres_text": acres_text(acres),
            "zones": [numbers.get(f.id) for f in items],
            "observations": observations,
            "causes": causes,
            "actions": actions,
            "observation_line": observation_line(items, acres, observations, causes),
            "action_line": action_line(items, actions, causes),
        })

    groups.sort(key=lambda g: (-g["acres"], -g["count"]))
    return groups


def observation_line(items, acres, observations, causes):
    """
    One pattern-level sentence, assembled only from what the agronomist wrote.

    Reads as an observation rather than a verdict — "associated with", not
    "caused by" — because association is the strongest claim a marked-up aerial
    image and a walk of the field actually support.
    """
    n = len(items)
    area = f" ({acres_text(acres)} acres)" if acres else ""
    subject = (sentence_case(join_phrases(observations, "or"))
               if observations else "Areas were flagged")

    if n == 1:
        stem = f"{subject} in one area{area}"
    else:
        stem = f"{subject} across {number_word(n)} areas{area}"

    if causes:
        return f"{stem}, associated with {join_phrases(causes)}."
    return f"{stem}."


def action_line(items, actions, causes):
    """
    What was recommended for this group, stated as a recommendation on record.

    It reports the agronomist's own recommendation and the cause most often
    noted beside it. It does not decide what the farmer should do, and it never
    reaches for a recommendation the annotations do not contain.
    """
    n = len(items)
    if not actions:
        return ("No recommendation was recorded against "
                f"{'this area' if n == 1 else 'these areas'} yet.")

    what = sentence_case(join_phrases(actions))
    where = "this area" if n == 1 else f"these {number_word(n)} areas"
    if causes:
        return (f"{what} was the recommendation recorded against {where}, "
                f"alongside {join_phrases(causes)}.")
    return f"{what} was the recommendation recorded against {where}."


def headline(groups, total_flagged, flagged_acres, field_acres):
    """
    The opening line of page 1: how much was marked, and into how many kinds.

    Deliberately a count and an area rather than a score. A single number out
    of 100 implies a precision the underlying data has not got, and it invites
    the farmer to argue with the number instead of reading the field.
    """
    if not total_flagged:
        return "No areas were marked on this flight."

    areas = f"{total_flagged} area{'' if total_flagged == 1 else 's'}"
    if flagged_acres and field_acres and flagged_acres <= field_acres:
        scope = (f", covering about {one_dp(flagged_acres):.1f} of the "
                 f"{field_acres:g} acres scouted")
    elif flagged_acres:
        scope = f", covering about {one_dp(flagged_acres):.1f} acres"
    else:
        scope = ""

    kinds = len(groups)
    tail = (f" They fall into {number_word(kinds)} "
            f"categor{'y' if kinds == 1 else 'ies'}, summarised below.")
    return f"This flight marked {areas} across the field{scope}." + tail


# ---------------------------------------------------------------- pagination
# Page 3 is a compact table rather than a card per zone, so far more fits on a
# sheet — but a long flight still has to break somewhere sensible. Heights are
# in millimetres and are measured a little generously, so packing errs towards
# leaving white space rather than pushing a row off the bottom of a page.
SHEET_BODY_MM = 246.0        # usable height below the running head
FIRST_SHEET_EXTRA_MM = 18.0  # the section heading and intro line, sheet one only
GROUP_HEAD_MM = 5.0          # the pattern heading above each table
TABLE_HEAD_MM = 6.0          # the column header row
ROW_MM = 6.2                 # one single-line row, padding included
ROW_LINE_MM = 3.4            # each extra wrapped line
GROUP_GAP_MM = 3.5           # the space below a finished table

# Characters that fit on one line of each text column, measured against a real
# WeasyPrint render at 8.5px rather than guessed. Under-stating these is the
# safe direction: it over-estimates the rows and breaks a page early, where
# over-stating them would let a row fall off the bottom of a sheet that clips.
COL_CHARS = {"observation": 26, "likely_cause": 33, "recommendation": 41}


def row_height(finding):
    lines = 1
    for field, chars in COL_CHARS.items():
        text = (getattr(finding, field, "") or "").strip()
        lines = max(lines, math.ceil(len(text) / chars) if text else 1)
    return ROW_MM + (lines - 1) * ROW_LINE_MM


def paginate_groups(groups, budget=SHEET_BODY_MM, first_extra=FIRST_SHEET_EXTRA_MM):
    """
    Pack the detail tables into sheets, splitting a long group across sheets
    rather than letting it overflow one.

    A group that continues onto the next sheet repeats its heading with
    "continued", so a row is never orphaned under someone else's heading.
    Returns a list of sheets; each sheet is a list of
    {group, rows, continued} blocks.
    """
    # Sheet one also carries the section heading and the intro line, so it has
    # less room for tables than the sheets that follow it.
    sheets, current, used = [], [], first_extra

    def room():
        return budget - used

    def flush():
        nonlocal current, used
        if current:
            sheets.append(current)
        current, used = [], 0.0

    for group in groups:
        rows, continued = [], False
        head = GROUP_HEAD_MM + TABLE_HEAD_MM
        if current and head + ROW_MM > room():
            flush()
        used += head

        for finding in group["findings"]:
            h = row_height(finding)
            if rows and h > room():
                current.append({"group": group, "rows": rows, "continued": continued})
                flush()
                rows, continued = [], True
                used += GROUP_HEAD_MM + TABLE_HEAD_MM
            rows.append(finding)
            used += h

        current.append({"group": group, "rows": rows, "continued": continued})
        used += GROUP_GAP_MM

    flush()
    return sheets or [[]]
