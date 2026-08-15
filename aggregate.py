"""
The V2 aggregation layer.

V1 printed the agronomist's fifteen annotations fifteen times. Nine of them
were near-identical rows under one broad label, which made a varied field read
as one repeated sentence and buried the single largest finding on the farm.

This module is the step that sits between the annotations and the report. It
groups what the agronomist already wrote into the patterns actually present in
their own text, counts the areas and acres in each, and composes one
pattern-level sentence and one suggested next step per pattern.

Three rules govern everything below.

1. It only ever summarises and combines what the agronomist wrote.
   No cause, diagnosis or recommendation is introduced here that does not
   already appear in the source annotations. Every phrase used in a generated
   sentence is carried on the pattern as `source_phrases`, so a reviewer — or a
   test — can check that claim mechanically.

2. It is deterministic.
   The same annotations produce the same report every time. Classification is
   evidence counting over the agronomist's own words, not a model call, which
   is what makes rule 1 checkable rather than merely intended.

3. It does not change how the agronomist works.
   Input is exactly what DroneDeploy already captures and the review screen
   already stores: category, observation, likely cause, recommendation, area.

The internal severity colour code (parsing.COLOUR_CODE — blue/green/yellow/red
by urgency) is untouched and stays in the internal screens. The report-facing
palette below is a separate system, keyed by category, because what a farmer
needs to read off a map is what kind of thing was seen and where, not how
urgent somebody graded it.
"""
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ---------------------------------------------------------------- categories
# Report-facing categories and their map colours. Order matters: it is the
# order categories appear on page 1 when two patterns are the same size, and
# the order of the legend on page 2.
#
# No colour here means "healthy" or "fine". Every colour on the map marks
# something the agronomist actually flagged.
CATEGORY_ORDER = [
    "irrigation", "soil_fertility", "crop_establishment",
    "pest_disease", "weeds", "needs_investigation",
]

CATEGORIES = {
    "irrigation": {
        "key": "irrigation",
        "label": "Irrigation / Moisture",
        "short": "Irrigation",
        "colour": "#2E6FBA",
        "soft": "#E8F0F9",
        "line": "#C3D8EE",
    },
    "soil_fertility": {
        "key": "soil_fertility",
        "label": "Soil Fertility / Nutrition",
        "short": "Soil fertility",
        "colour": "#D9A32B",
        "soft": "#FBF3DF",
        "line": "#EEDCAA",
    },
    "crop_establishment": {
        "key": "crop_establishment",
        "label": "Crop Establishment",
        "short": "Establishment",
        "colour": "#B2532F",
        "soft": "#F8ECE6",
        "line": "#E7C9BB",
    },
    "pest_disease": {
        "key": "pest_disease",
        "label": "Pest / Disease",
        "short": "Pest / disease",
        "colour": "#9E2430",
        "soft": "#F8E9EA",
        "line": "#E5C3C6",
    },
    "weeds": {
        "key": "weeds",
        "label": "Weeds",
        "short": "Weeds",
        "colour": "#5E7355",
        "soft": "#EDF1EA",
        "line": "#CFD9C8",
    },
    "needs_investigation": {
        "key": "needs_investigation",
        "label": "Needs Investigation",
        "short": "Needs investigation",
        "colour": "#6B4C9A",
        "soft": "#F0EBF7",
        "line": "#D5C8E8",
    },
}

# Where the agronomist's own category enum lands when their text gives no
# clearer signal. This is a fallback, not the primary route: V1 showed that the
# enum is often a single broad label across a whole flight, which is exactly
# the flattening V2 exists to undo.
ENUM_FALLBACK = {
    "irrigation": "irrigation",
    "drainage / soil": "soil_fertility",
    "nutrient / vigor": "soil_fertility",
    "nutrient / vigour": "soil_fertility",
    "pest / disease": "pest_disease",
    "planting gap": "crop_establishment",
    "needs investigation": "needs_investigation",
}

# ---------------------------------------------------------------- evidence
# Phrases that indicate a category, matched against the agronomist's own words.
#
# They are phrases rather than single words wherever a single word would be
# ambiguous: "soil" appears in both "soil fertility" and "excess soil cover",
# which are two different findings, so neither list claims the bare word.
KEYWORDS = {
    "irrigation": [
        "water stress", "waterlogg", "water logg", "moisture", "irrigation",
        "irrigated", "drip", "sprinkler", "watering", "water distribution",
        "water pressure", "low pressure", "drought", "dry spell", "water",
    ],
    "soil_fertility": [
        "soil fertility", "fertility", "soil condition", "soil test",
        "soil analys", "soil sampl", "nutrient", "nutrition", "deficien",
        "fertiliser", "fertilizer", "npk", "nitrogen", "phosphor", "potassium",
        "manure", "compost", "organic matter", "top dress", "topdress",
        "uptake", "soil ph",
    ],
    "crop_establishment": [
        "germinat", "emergence", "emerg", "mound", "ridging", "soil cover",
        "plant population", "planting", "replant", "seedling", "sowing",
        "seed rate", "spacing", "establishment", "stand count", "planting gap",
        "crop cover", "gap",
    ],
    "pest_disease": [
        "pest", "disease", "aphid", "blight", "mildew", "rust", "virus",
        "fungal", "fungus", "fungicide", "insecticide", "rot ", "rotting",
        "worm", "larva", "caterpillar", "borer", "mite", "nematode",
        "infestation", "lesion", "scout", "spray", "wilt",
    ],
    "weeds": [
        "weed", "striga", "couch grass",
    ],
    "needs_investigation": [
        "unclear", "unknown", "undetermined", "not established", "unconfirmed",
        "further investigation", "to be confirmed", "cannot tell", "uncertain",
    ],
}

# The agronomist's likely cause is the strongest signal, then what they
# recommended doing about it, then what they saw. A symptom ("poor vigour")
# says less about the pattern than the cause they attributed it to.
FIELD_WEIGHTS = (("likely_cause", 3), ("recommendation", 2), ("observation", 1))

# How alike two annotations' cause-and-recommendation wording must be to belong
# to the same pattern inside a category. Measured as Jaccard overlap of content
# words. Deliberately low: the job is to separate genuinely different stories
# (overmounding from soil fertility), not to split hairs over phrasing.
SIMILARITY = 0.25

# Ceiling on how many patterns page 1 tells. Above this the smallest patterns
# are folded into the largest pattern *of their own category* — never across
# categories, which is the mistake V1 made.
MAX_PATTERNS = 6

# How many pattern paragraphs and suggestions page 1 has room for.
MAX_OBSERVATIONS = 5
MAX_SUGGESTIONS = 5

_STOPWORDS = {
    "a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with",
    "from", "by", "or", "vs", "versus", "is", "are", "was", "were", "be",
    "this", "that", "these", "those", "it", "its", "as", "than", "then",
    "some", "any", "there", "here", "along", "across", "into", "onto",
    "other", "another", "more", "most", "very", "also", "due", "possible",
    "possibly", "likely", "suspected", "check", "one", "two",
}

_SPLIT_RE = re.compile(r"\s*(?:[,;/|]|\u2014|\u2013| - | and | plus )\s*", re.I)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")

_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}


# ---------------------------------------------------------------- text utils
def clean(text):
    """Collapse whitespace and strip list punctuation from the edges."""
    return re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n.;,:-\u2013\u2014")


def phrases(text):
    """
    Split one of the agronomist's fields into its separate statements.

    "Overmounding / excess soil cover, water stress" is three things they
    noticed, not one, and the report should be able to name them separately
    without inventing a fourth.
    """
    out = []
    for part in _SPLIT_RE.split(str(text or "")):
        part = clean(part)
        if part and len(part) > 1:
            out.append(part)
    return out


def sentence_case(text):
    """Capitalise the first letter and leave the rest of the writer's case alone."""
    text = clean(text)
    return text[:1].upper() + text[1:] if text else text


def lower_first(text):
    """
    Lowercase an opening word so a phrase can sit mid-sentence — unless it is
    an acronym or a proper noun the agronomist capitalised deliberately (NPK,
    Striga), which stays as written.
    """
    text = clean(text)
    if not text:
        return text
    first = text.split(" ", 1)[0]
    if first.isupper() or (len(first) > 1 and first[1:].lower() != first[1:]):
        return text
    return text[:1].lower() + text[1:]


def number_word(n):
    """Counts read as words up to twelve, the way the agronomist would say them."""
    return _NUMBER_WORDS.get(n, str(n))


def acres(value):
    """
    Format an acreage for the report.

    Small areas keep two decimals because 0.01 ac is a real annotation and
    "0.0 ac" would read as nothing at all; everything else takes one.

    Rounded half-up rather than through float formatting: fifteen areas summing
    to exactly 19.15 land on a binary value a hair under it, and Python would
    print 19.1 for a figure everybody checking the arithmetic by hand makes
    19.2. On a document whose whole argument is that the numbers can be
    checked, that is worth the Decimal.
    """
    try:
        value = Decimal(str(float(value or 0)))
    except (TypeError, ValueError, InvalidOperation):
        return "0"
    places = Decimal("0.01") if value and abs(value) < Decimal("0.1") else Decimal("0.1")
    out = str(value.quantize(places, rounding=ROUND_HALF_UP))
    # A 38-acre field is 38 acres, not 38.0 — the decimal implies a precision
    # the figure on the farm record does not have.
    return out[:-2] if out.endswith(".0") else out


def acres_exact(value):
    """
    A single area as measured, not as summed.

    Pattern totals are rounded because they are estimates of a group; an
    individual zone is a figure DroneDeploy measured, and 0.02 ac rounding to
    0.0 in the detailed table would misreport it. Two decimals throughout, so
    the column adds up when somebody checks it.
    """
    try:
        value = Decimal(str(float(value or 0)))
    except (TypeError, ValueError, InvalidOperation):
        return "—"
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pos(value):
    try:
        value = float(value)
        return value if value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _words(text):
    """Content words of a phrase, lowercased, stopwords dropped."""
    return {w.lower() for w in _WORD_RE.findall(str(text or ""))
            if w.lower() not in _STOPWORDS and len(w) > 2}


def _rank_phrases(items, limit=None):
    """
    Distinct phrases in frequency order, ties broken by first appearance.

    Case and trailing punctuation are ignored when comparing, and the first
    spelling the agronomist used is the one kept.
    """
    seen, order = {}, []
    for i, phrase in enumerate(items):
        key = re.sub(r"[^a-z0-9 ]", "", phrase.lower()).strip()
        if not key:
            continue
        if key not in seen:
            seen[key] = {"text": phrase, "count": 0, "first": i}
            order.append(key)
        seen[key]["count"] += 1
    ranked = sorted((seen[k] for k in order),
                    key=lambda d: (-d["count"], d["first"]))
    ranked = _drop_nested(ranked)
    return ranked[:limit] if limit else ranked


def _drop_nested(ranked):
    """
    Remove a phrase already said by one above it.

    "Soil condition/fertility" splits into two statements, and the bare
    "fertility" half then sits in the list beside "soil fertility" from another
    area — which would print as "soil fertility or fertility". Where one
    phrase's words are wholly contained in a stronger one's, the stronger one
    already carries it.
    """
    kept = []
    for item in ranked:
        words = _words(item["text"])
        if words and any(words <= _words(k["text"]) for k in kept):
            continue
        kept.append(item)
    return kept


def join_phrases(items, conjunction="or"):
    """'a', 'a or b', 'a, b or c' — the way the phrases would be read aloud."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]


# ---------------------------------------------------------------- classify
def classify(finding):
    """
    Which report category this annotation belongs to, and why.

    Evidence is counted one statement at a time, not one keyword at a time.
    "Nutrient deficiencies, soil condition" is two soil statements; "nutrient
    uptake" is one, even though two separate words in it appear on the soil
    list. Counting keywords instead lets the length of the list below outvote
    what the agronomist actually wrote, which is the opposite of the intent.

    Returns (category_key, evidence) where evidence lists the matching phrases
    per category, so the decision can be inspected rather than trusted.
    """
    scores = {k: 0 for k in CATEGORY_ORDER}
    evidence = {k: [] for k in CATEGORY_ORDER}
    earliest = {}

    for field, weight in FIELD_WEIGHTS:
        for position, part in enumerate(phrases(getattr(finding, field, ""))):
            low = part.lower()
            for key in CATEGORY_ORDER:
                if any(word in low for word in KEYWORDS[key]):
                    scores[key] += weight
                    evidence[key].append(part)
                    if field == "likely_cause" and key not in earliest:
                        earliest[key] = position

    best = max(scores.values())
    if best == 0:
        enum = str(getattr(finding, "category", "") or "").strip().lower()
        return ENUM_FALLBACK.get(enum, "needs_investigation"), evidence

    winners = [k for k in CATEGORY_ORDER if scores[k] == best]
    if len(winners) == 1:
        return winners[0], evidence

    # A tie means the agronomist named two things. The one they wrote first in
    # the likely cause leads — "Inadequate moisture, nutrient uptake" is a
    # moisture note that mentions uptake, not the other way round.
    placed = [k for k in winners if k in earliest]
    if placed:
        return min(placed, key=lambda k: earliest[k]), evidence

    enum = str(getattr(finding, "category", "") or "").strip().lower()
    fallback = ENUM_FALLBACK.get(enum)
    if fallback in winners:
        return fallback, evidence
    return winners[0], evidence


# ---------------------------------------------------------------- clustering
def _signature(finding):
    """The words a pattern is judged on: the cause, and what was advised."""
    return _words(getattr(finding, "likely_cause", "")) | _words(
        getattr(finding, "recommendation", ""))


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _cluster(findings, threshold=SIMILARITY):
    """
    Single-linkage grouping of annotations that tell the same story.

    Single linkage rather than a centroid: nine soil notes worded nine slightly
    different ways form one chain, and requiring every pair to match would
    split them into a list that reads exactly like the V1 report this replaces.
    """
    n = len(findings)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    sigs = [_signature(f) for f in findings]
    for i in range(n):
        for j in range(i + 1, n):
            if _similarity(sigs[i], sigs[j]) >= threshold:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(findings[i])
    return [groups[k] for k in sorted(groups)]


# ---------------------------------------------------------------- sentences
def _pattern_text(cat, zones, zone_numbers):
    """
    One pattern-level sentence, built only from the agronomist's own phrases.

    Returns the parts separately so the report can set the opening clause in
    bold without the template having to cut the sentence up itself.
    """
    observations = [clean(z.observation) for z in zones if clean(z.observation)]
    cause_bits, action_bits = [], []
    for z in zones:
        cause_bits += phrases(z.likely_cause)
        action_bits += phrases(z.recommendation)

    ranked_obs = _rank_phrases(observations)
    ranked_causes = _rank_phrases(cause_bits, limit=3)

    # A second recommendation is only named when it is actually common to the
    # pattern. One "weeding" among nine "soil testing" entries is a note about
    # one area, and hoisting it into the pattern's suggested action would put
    # words in the agronomist's mouth about the other eight.
    ranked_actions = [a for a in _rank_phrases(action_bits)
                      if a["count"] >= max(1, math.ceil(len(zones) / 3.0))][:2]

    lead_obs = ranked_obs[0]["text"] if ranked_obs else cat["label"]
    causes = join_phrases([lower_first(c["text"]) for c in ranked_causes])
    total = sum(_pos(z.area_acres) for z in zones)
    n = len(zones)

    if n == 1:
        lead = f"One area (~{acres(total)} acres)"
        body = f" recorded {lower_first(lead_obs)}"
    else:
        lead = f"{sentence_case(lead_obs)} across {number_word(n)} areas (~{acres(total)} acres)"
        body = ""

    # The attribution ("as recorded by the agronomist") is stated once, above
    # the whole section, rather than tacked onto every sentence. Repeated five
    # times it stops reading as provenance and starts reading as a disclaimer.
    if causes:
        body += f", associated with {causes}." if n == 1 else \
                f" was associated with {causes}."
    else:
        body += "." if n == 1 else ". No cause was recorded against these areas."

    return {
        "lead": lead,
        "body": body,
        "sentence": lead + body,
        "causes": [c["text"] for c in ranked_causes],
        "actions": [a["text"] for a in ranked_actions],
        "source_phrases": ([o["text"] for o in ranked_obs]
                           + [c["text"] for c in ranked_causes]
                           + [a["text"] for a in ranked_actions]),
    }


def _suggestion(cat, zones, zone_numbers, text):
    """
    One suggested area to investigate, phrased as an option.

    It restates the step the agronomist already recommended most often in this
    pattern and offers it as something to consider. Nothing new is proposed
    here: if they recorded no recommendation, that absence is what gets said.
    """
    actions = text["actions"]
    causes = text["causes"]
    n = len(zones)

    if not actions:
        where = f"Zone {zone_numbers[0]}" if n == 1 else f"{number_word(n)} areas"
        return (f"No recommendation was recorded against {where} in the "
                f"{cat['label'].lower()} pattern — worth a second look before the next flight.")

    step = join_phrases([lower_first(a) for a in actions], conjunction="and")
    cause = lower_first(causes[0]) if causes else ""

    if n == 1:
        tail = (f", given the recorded cause — {cause} — sits apart from the "
                "rest of the field" if cause else "")
        return (f"{sentence_case(step)} in the single affected area "
                f"(Zone {zone_numbers[0]}, ~{acres(sum(_pos(z.area_acres) for z in zones))} acres) "
                f"may be worth considering{tail}.")

    tail = (f" whether {cause} is the main factor across them"
            if cause else " what those areas have in common")
    return (f"{sentence_case(step)} in a few representative areas of the "
            f"{number_word(n)}-area {cat['short'].lower()} pattern "
            f"could help clarify{tail}.")


# ---------------------------------------------------------------- assembly
def build(flight, findings=None):
    """
    The whole report model for one flight.

    Everything the three pages need, computed once. Page 3 keeps every original
    annotation exactly as the agronomist wrote it; pages 1 and 2 are built from
    the patterns found across them.
    """
    findings = list(findings if findings is not None else flight.findings)

    # Zones are numbered largest area first, so zone 1 is the biggest thing on
    # the farm and the numbering means something when read off the map.
    ordered = sorted(findings,
                     key=lambda f: (-_pos(f.area_acres), f.sort_order or 0, f.id or 0))
    numbers = {f.id: i + 1 for i, f in enumerate(ordered)}

    # ---- classify, then find the patterns inside each category --------------
    by_category = {}
    classified = {}
    for f in ordered:
        key, evidence = classify(f)
        classified[f.id] = {"category": key, "evidence": evidence}
        by_category.setdefault(key, []).append(f)

    patterns = []
    for key in CATEGORY_ORDER:
        zones = by_category.get(key)
        if not zones:
            continue
        cat = CATEGORIES[key]
        for group in _cluster(zones):
            group = sorted(group, key=lambda f: (-_pos(f.area_acres), numbers[f.id]))
            patterns.append({
                "category": key,
                "label": cat["label"],
                "short": cat["short"],
                "colour": cat["colour"],
                "soft": cat["soft"],
                "line": cat["line"],
                "zones": group,
                "acres": sum(_pos(z.area_acres) for z in group),
            })

    patterns = _fold_smallest(patterns)
    patterns.sort(key=lambda p: (-p["acres"], -len(p["zones"]),
                                 CATEGORY_ORDER.index(p["category"])))

    for i, p in enumerate(patterns):
        p["index"] = i + 1
        p["count"] = len(p["zones"])
        p["acres_text"] = acres(p["acres"])
        p["zone_numbers"] = [numbers[z.id] for z in p["zones"]]
        text = _pattern_text(CATEGORIES[p["category"]], p["zones"], p["zone_numbers"])
        p.update(text)
        p["suggestion"] = _suggestion(CATEGORIES[p["category"]], p["zones"],
                                      p["zone_numbers"], text)

    # ---- category totals for the summary cards ------------------------------
    cards = []
    for key in CATEGORY_ORDER:
        zones = by_category.get(key)
        if not zones:
            continue
        cat = CATEGORIES[key]
        total = sum(_pos(z.area_acres) for z in zones)
        cards.append({**cat, "count": len(zones), "acres": total,
                      "acres_text": acres(total)})
    cards.sort(key=lambda c: (-c["count"], -c["acres"]))

    marked = sum(_pos(f.area_acres) for f in ordered)
    scouted = _pos(getattr(flight, "acreage", None)) or _pos(
        getattr(flight.farm, "acreage", None))

    return {
        "zones": ordered,
        "numbers": numbers,
        "classified": classified,
        "patterns": patterns,
        "observations": patterns[:MAX_OBSERVATIONS],
        "extra_patterns": patterns[MAX_OBSERVATIONS:],
        "suggestions": [p for p in patterns[:MAX_SUGGESTIONS]],
        "cards": cards,
        "category_of": lambda f: CATEGORIES[classified[f.id]["category"]],
        "number_of": lambda f: numbers[f.id],
        "count": len(ordered),
        "marked_acres": marked,
        "marked_text": acres(marked),
        "scouted_acres": scouted,
        "scouted_text": acres(scouted) if scouted else "",
        "has_areas": marked > 0,
        "categories_present": len(cards),
        "CATEGORIES": CATEGORIES,
        "CATEGORY_ORDER": CATEGORY_ORDER,
        "acres": acres,
        "acres_exact": acres_exact,
        "number_word": number_word,
        "groups": _group_rows(patterns),
    }


def _fold_smallest(patterns):
    """
    Keep the number of patterns readable, without flattening the field.

    When a category has produced more clusters than page 1 can carry, its
    smallest cluster is folded into its own largest one. A pattern is never
    folded into a different category: the 4.1-acre overmounding area is not a
    soil fertility problem just because folding it there would tidy the page.
    """
    patterns = list(patterns)
    while len(patterns) > MAX_PATTERNS:
        by_cat = {}
        for p in patterns:
            by_cat.setdefault(p["category"], []).append(p)
        candidates = [group for group in by_cat.values() if len(group) > 1]
        if not candidates:
            break                      # every remaining pattern is its own category
        group = min(candidates, key=lambda g: min(p["acres"] for p in g))
        smallest = min(group, key=lambda p: (p["acres"], len(p["zones"])))
        largest = max((p for p in group if p is not smallest),
                      key=lambda p: (p["acres"], len(p["zones"])))
        largest["zones"] = largest["zones"] + smallest["zones"]
        largest["acres"] += smallest["acres"]
        patterns.remove(smallest)
    return patterns


def _group_rows(patterns):
    """
    Page 3, grouped under the pattern each annotation belongs to.

    Same objects as `patterns`, exposed separately so the detailed-findings
    page can iterate without the template reaching into page 1's structures.
    """
    return [{
        "pattern": p,
        "title": p["label"],
        "colour": p["colour"],
        "soft": p["soft"],
        "line": p["line"],
        "count": len(p["zones"]),
        "acres_text": acres(p["acres"]),
        "rows": p["zones"],
    } for p in patterns]


# ---------------------------------------------------------------- guardrail
def unsourced_phrases(pattern):
    """
    Every phrase the generated sentence used that is not in the source text.

    Returns a list, and an empty list is the pass condition. This is the
    guardrail from the specification made checkable: the summary may only
    contain the agronomist's own causes and recommendations, so a
    non-empty result here is a bug, not a style note.
    """
    source = " ".join(
        " ".join(str(getattr(z, f, "") or "") for f in
                 ("observation", "likely_cause", "recommendation"))
        for z in pattern["zones"]).lower()
    missing = []
    for phrase in pattern.get("source_phrases", []):
        if clean(phrase).lower() not in source:
            missing.append(phrase)
    return missing


# ---------------------------------------------------------------- pagination
# Page 3 is packed into sheets here rather than left to the renderer, so the
# page breaks on screen are the page breaks on paper. Heights are in
# millimetres, measured against a real WeasyPrint render of the findings table
# and rounded down, so the packer errs towards white space rather than towards
# a row that does not fit.
SHEET_BODY_MM = 226.0        # usable height on a detailed-findings sheet
GROUP_HEAD_MM = 13.0         # the pattern heading and its table header row
ROW_BASE_MM = 5.4            # one single-line row, including its padding
LINE_MM = 3.9                # each additional wrapped line
COLUMN_CHARS = {"observation": 30, "likely_cause": 34, "recommendation": 34}


def row_height(finding):
    lines = 1
    for field, width in COLUMN_CHARS.items():
        text = str(getattr(finding, field, "") or "").strip()
        lines = max(lines, math.ceil(len(text) / width) if text else 1)
    return ROW_BASE_MM + (lines - 1) * LINE_MM


def paginate(groups, budget=SHEET_BODY_MM):
    """
    Pack the grouped findings onto sheets, filling each one before the next.

    A group can be split across sheets — the continuation is marked so the
    reader knows the heading above it is the same pattern — but a row is never
    split, because half a recommendation is worse than a shorter page.
    """
    if not groups:
        return [[]]

    pages, current, used = [], [], 0.0
    for group in groups:
        rows, first_slice = list(group["rows"]), True
        while rows:
            if used + GROUP_HEAD_MM + ROW_BASE_MM > budget and current:
                pages.append(current)
                current, used = [], 0.0
            block = {"group": group, "rows": [], "continued": not first_slice}
            used += GROUP_HEAD_MM
            while rows:
                h = row_height(rows[0])
                if block["rows"] and used + h > budget:
                    break
                block["rows"].append(rows.pop(0))
                used += h
            current.append(block)
            first_slice = False
            if rows:                          # ran out of sheet mid-group
                pages.append(current)
                current, used = [], 0.0
    if current:
        pages.append(current)
    return pages or [[]]
