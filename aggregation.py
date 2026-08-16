"""
Grouping a flight's findings into the patterns the V2 report is built from.

V1 listed all fifteen zones one after another, nine of them near-identical
entries under a single category. That is an annotation dump, not a report. This
turns the same rows into the small number of patterns actually present in them.

The rule this module exists to enforce:

    Only summarise and combine what the agronomist actually wrote. Never
    introduce a cause, a diagnosis or a recommendation that does not already
    appear in the source annotations.

So there is no model here and no generated prose in the sense of invention.
Sentences are assembled from the agronomist's own counts, acreages and words.
Where a phrase has to be chosen — the name of a pattern, say — it is chosen from
a fixed vocabulary keyed off the words already in the likely-cause text, never
written fresh.

The report-facing categories are separate from the internal severity colours the
agronomists use while annotating. That code stays as it is for triage; this maps
into a farmer-facing set that answers "what kind, and where" rather than "how
urgent".
"""
import decimal
import re

from markupsafe import Markup, escape

# ----------------------------------------------------------------- categories
# The report-facing set, in the order they appear in the legend. Colour is by
# category rather than by urgency: nothing here means "healthy", because every
# colour on the map marks something the agronomist chose to flag.
REPORT_CATEGORIES = [
    ("Irrigation / Moisture",     "#2F6FDB"),   # blue
    ("Soil Fertility / Nutrition", "#D9A441"),  # amber
    ("Crop Establishment",        "#B5543A"),   # terracotta
    ("Pest / Disease",            "#A32B32"),   # deep red
    ("Weeds",                     "#5C7A5C"),   # slate green
    ("Needs Investigation",       "#6B4E8C"),   # purple
]
CATEGORY_COLOURS = dict(REPORT_CATEGORIES)
CATEGORY_ORDER = [name for name, _ in REPORT_CATEGORIES]

# Where the agronomist's own category lands when it is already one of the six.
DIRECT_MAP = {
    "irrigation": "Irrigation / Moisture",
    "irrigation / moisture": "Irrigation / Moisture",
    "drainage / soil": "Soil Fertility / Nutrition",
    "nutrient / vigor": "Soil Fertility / Nutrition",
    "nutrient / vigour": "Soil Fertility / Nutrition",
    "soil fertility / nutrition": "Soil Fertility / Nutrition",
    "planting gap": "Crop Establishment",
    "crop establishment": "Crop Establishment",
    "pest / disease": "Pest / Disease",
    "weeds": "Weeds",
    "needs investigation": "Needs Investigation",
}

# Words in the agronomist's likely-cause text that identify the real pattern.
# This is what separates irrigation from soil fertility when both were filed
# under one broad category, which is exactly what happened on the IPM flight.
#
# Ordered most specific first: a cause naming both mounding and water stress is
# a crop establishment problem, because the mounding is the thing being blamed.
# Ordered most specific first, and the first match wins.
#
# The order is a judgement rather than an accident. A cause naming mounding is a
# crop establishment problem even when it also mentions water stress, because
# the mounding is the thing being blamed. A cause naming an organism is a pest
# problem even when it mentions the weedy edge that organism came in from — so
# Weeds sits last and claims a finding only when nothing else explains it.
#
# Keywords are matched as substrings, so each has to be long enough not to fire
# inside an unrelated word. "ph" used to sit in the fertility list and matched
# the "ph" in "aphid"; it is now "soil ph".
CAUSE_PATTERNS = [
    ("Crop Establishment", [
        "overmound", "over-mound", "mounding", "mound height", "mound",
        "excess soil cover", "soil cover", "planting depth", "planting gap",
        "germinat", "establishment", "established", "establish", "replant",
        "seedling", "transplant", "poor stand", "crop stand",
    ]),
    ("Pest / Disease", [
        "pest", "disease", "aphid", "thrips", "whitefly", "caterpillar",
        "larvae", "nematode", "beetle", "insect", "infestation", "blight",
        "fungal", "fungus", "mildew", "leaf spot", "leaf-spot", "spotting",
        "lesion", "wilt", "virus", "bacterial", "root rot", "soft rot",
        "rotting", "borer", "mites",
    ]),
    ("Irrigation / Moisture", [
        "water stress", "irrigation", "drip", "sprinkler", "moisture",
        "waterlog", "water-log", "short of water", "watering", "drought",
        "dry patch", "dry wedge", "drying", "low pressure", "pressure at",
        "overwater", "under-water", "flooding",
    ]),
    ("Soil Fertility / Nutrition", [
        "soil fertility", "fertility", "nutrient", "nutrition", "soil condition",
        "deficien", "uptake", "nitrogen", "phosph", "potass", "npk", "manure",
        "fertiliser", "fertilizer", "top-dress", "topdress", "soil ph",
        "acidity", "organic matter",
    ]),
    ("Weeds", [
        "weed",
    ]),
]


def _norm(text):
    return " ".join(str(text or "").lower().split())


def classify(finding):
    """
    The report category for one finding.

    The likely cause is read first, because it is where the agronomist recorded
    what they actually think is happening. The category they picked is the
    fallback, since in V1 it was often the same broad label for everything.
    """
    cause = _norm(finding.likely_cause)
    obs = _norm(finding.observation)
    haystack = f"{cause} {obs}"

    if cause:
        for name, words in CAUSE_PATTERNS:
            if any(w in cause for w in words):
                return name

    direct = DIRECT_MAP.get(_norm(finding.category))
    if direct:
        return direct

    for name, words in CAUSE_PATTERNS:
        if any(w in haystack for w in words):
            return name
    return "Needs Investigation"


# ----------------------------------------------------------------- phrasing
def _acres(value):
    """
    Acreage as the report writes it.

    One decimal down to an acre, two below that, so a 0.01-acre weed patch is
    not rounded away to nothing and 13.36 does not read as a flat 13.
    """
    if value is None:
        return None
    if value >= 1:
        # Round half up rather than to even: 19.15 reads as 19.2, which is what
        # a person doing the arithmetic by hand would write.
        return f"{decimal.Decimal(str(value)).quantize(decimal.Decimal('0.1'), rounding=decimal.ROUND_HALF_UP)}"
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0.01"


def _count_word(n):
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
             7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve"}
    return words.get(n, str(n))


def _phrases(findings, field, limit=3):
    """
    The distinct things the agronomist wrote in one field, most common first.

    Returned as their own words. Nothing is paraphrased, because a paraphrase is
    where an unsupported claim would creep in.
    """
    seen = {}
    for f in findings:
        raw = (getattr(f, field, "") or "").strip().rstrip(".")
        if not raw:
            continue
        key = _norm(raw)
        if key not in seen:
            seen[key] = [raw, 0]
        seen[key][1] += 1
    # Most common first, and on a tie the order they arrived in — the findings
    # are sorted largest-first, so the biggest area leads the sentence rather
    # than whichever one happens to sort earliest alphabetically.
    ordered = sorted(seen.values(), key=lambda x: -x[1])
    # Drop a phrase already contained in a longer one that is being kept:
    # "plant vigour" alongside "poor plant vigour" says the same thing twice.
    kept = []
    for text, _n in ordered:
        low = _norm(text)
        if any(low in _norm(k) or _norm(k) in low for k in kept):
            continue
        kept.append(text)
        if len(kept) >= limit:
            break
    return kept


def _join(items):
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _observation_sentence(group):
    """
    One pattern-level sentence, assembled from the agronomist's own words.

    Deliberately hedged — "associated with", not "caused by" — because the
    annotation records what was seen and what the agronomist suspects, not a
    result. The report should not sound more certain than its source.
    """
    n = group["count"]
    acres = group["acres_text"]
    obs = _phrases(group["findings"], "observation", 2)
    causes = _phrases(group["findings"], "likely_cause", 2)

    if n == 1:
        head = f"One area (~{acres} acres)" if acres else "One area"
    else:
        head = (f"{_count_word(n).capitalize()} areas (~{acres} acres in total)" if acres
                else f"{_count_word(n).capitalize()} areas")
    head = Markup(f"<b>{escape(head)}</b>")

    obs_text = _join([o[0].lower() + o[1:] for o in obs]) if obs else "an observation worth noting"
    sentence = f"{head} showed {escape(obs_text)}"
    if causes:
        sentence += f", associated with {escape(_join([c[0].lower() + c[1:] for c in causes]))}"
    # Markup because the lead carries a <b>; everything interpolated around it is
    # escaped first, so the agronomist's own text can never inject markup.
    return Markup(sentence + ".")


def _suggestion(group):
    """
    What to look at, phrased as an option.

    Built from the recommendations the agronomist already wrote. Where they all
    wrote the same thing, that is the suggestion; where they differ, the
    distinct ones are offered together. Nothing new is proposed.
    """
    recs = _phrases(group["findings"], "recommendation", 3)
    biggest = group["findings"][0]
    zone = group["zones"][0]
    # Two forms: one that sits inside brackets already, and one that carries its
    # own, so neither ends up with nested parentheses or a run of commas.
    inline = f"zone {zone}"
    standalone = f"zone {zone}"
    if biggest.area_acres:
        inline += f", ~{_acres(biggest.area_acres)} acres"
        standalone += f" (~{_acres(biggest.area_acres)} acres)"

    if not recs:
        return (f"No next step was recorded against the {group['name'].lower()} "
                "areas, so they may be worth a closer look.")

    # Attributed rather than instructed: "the agronomist suggested" offers the
    # farmer what was written, where the imperative the agronomist used for
    # their own notes would read as an order.
    cleaned = [r[0].lower() + r[1:] for r in recs]
    if group["count"] == 1:
        return f"For the one area here ({inline}), the agronomist suggested: {cleaned[0]}."
    # Distinct recommendations are separated by semicolons rather than "and",
    # because several are themselves two clauses joined by "and" and chaining
    # them that way produced a sentence that ran on without a break in it.
    acres = f" (~{group['acres_text']} acres)" if group["acres_text"] else ""
    return (f"Across the {_count_word(group['count'])} areas in this pattern{acres}, "
            f"the agronomist suggested: {'; '.join(cleaned)}. The largest, "
            f"{standalone}, may be the most useful place to start.")


# ----------------------------------------------------------------- aggregation
def aggregate(findings, numbers=None):
    """
    Turn a flight's findings into report patterns.

    `numbers` maps a finding id to the zone number shown on the map, so the
    summary and the map agree. Returns None when there is nothing to group.
    """
    findings = [f for f in findings or [] if f is not None]
    if not findings:
        return None

    numbers = numbers or {}
    buckets = {}
    for f in findings:
        name = classify(f)
        buckets.setdefault(name, []).append(f)

    groups = []
    for name in CATEGORY_ORDER:
        rows = buckets.get(name)
        if not rows:
            continue
        rows = sorted(rows, key=lambda f: -(f.area_acres or 0))
        acres = sum((f.area_acres or 0) for f in rows)
        group = {
            "name": name,
            "colour": CATEGORY_COLOURS[name],
            "findings": rows,
            "count": len(rows),
            "acres": round(acres, 2),
            "acres_text": _acres(acres) if acres else "",
            "zones": [numbers.get(f.id, i + 1) for i, f in enumerate(rows)],
        }
        group["heading"] = (f"{group['name']} — {group['count']} "
                            f"area{'' if group['count'] == 1 else 's'}"
                            + (f", ~{group['acres_text']} ac" if group["acres_text"] else ""))
        group["observation"] = _observation_sentence(group)
        group["suggestion"] = _suggestion(group)
        groups.append(group)

    # Largest by area first: the biggest thing on the farm should lead, and on
    # the IPM flight that is exactly what stopped the 4.1-acre mounding zone
    # from being buried under nine smaller soil-fertility ones.
    groups.sort(key=lambda g: (-g["acres"], -g["count"]))

    total_acres = sum(g["acres"] for g in groups)
    return {
        "groups": groups,
        "total_findings": len(findings),
        "total_acres": round(total_acres, 2),
        "total_acres_text": _acres(total_acres) if total_acres else "",
        "category_count": len(groups),
        "legend": REPORT_CATEGORIES,
    }


def summary_sentence(agg, flight, farm):
    """The opening line of page one. Counts and acreage, no verdict."""
    if not agg:
        return "No areas were flagged on this flight."
    n = agg["total_findings"]
    scouted = farm.acreage or flight.acreage
    area_clause = ""
    if agg["total_acres_text"]:
        if scouted:
            scouted_text = (f"{scouted:g}" if float(scouted).is_integer()
                            else _acres(scouted))
            area_clause = (f", covering approximately {agg['total_acres_text']} of the "
                           f"{scouted_text} scouted acres")
        else:
            area_clause = f", covering approximately {agg['total_acres_text']} acres"
    cats = agg["category_count"]
    return (f"This flight identified {n} area{'' if n == 1 else 's'} across the field "
            f"showing some form of observation{area_clause}. "
            f"{'This falls' if cats == 1 else 'These fall'} into "
            f"{_count_word(cats)} broad categor{'y' if cats == 1 else 'ies'}, "
            "summarised below.")
