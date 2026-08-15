"""
Whether the aggregation layer holds up on flights it was not written against.

The reference flight in test_aggregate.py proves the layer reproduces the report
in the specification. It does not prove much else: it is one crop, one
agronomist, one vocabulary, and a module tuned until that one flight comes out
right is a module that works on that one flight.

So this file runs flights the module has never seen — other crops, other pests,
other regions, other habits of writing — and asserts the properties that have to
hold for *every* flight rather than the answers that happen to be right for one:

  * every annotation reaches a pattern, and reaches exactly one
  * every pattern has a heading that says something
  * every generated sentence traces back to the agronomist's own words
  * the voice never slips into instruction or alarm
  * the totals add up to what was recorded
  * the whole thing still renders

Then it does the same on flights designed to break it: empty text, one word,
duplicated text, hundreds of findings, missing areas, invented categories, and
a vocabulary with no overlap with this project at all.

Run it with:

    python tests/test_generalisation.py
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import aggregate as ag

P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label +
          ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


_next_id = [0]


class Row:
    def __init__(self, obs, cause, rec, acres, category=""):
        _next_id[0] += 1
        self.id = _next_id[0]
        self.sort_order = _next_id[0]
        self.category = category
        self.observation = obs
        self.likely_cause = cause
        self.recommendation = rec
        self.area_acres = acres


class Flight:
    def __init__(self, rows, acreage=50.0):
        self.acreage = acreage
        self.findings = rows
        self.farm = type("Farm", (), {"acreage": acreage})()


# ---------------------------------------------------------------- the flights
# Written the way a different agronomist, on a different crop, would write them.
# None of this wording was used while building the module.
FLIGHTS = {
    "maize / fall armyworm, Machakos": [
        Row("Windowpaning on young leaves", "Fall armyworm larvae in whorls",
            "Scout whorls and spray at threshold", 6.2, "Pest / Disease"),
        Row("Ragged leaf margins", "Fall armyworm feeding damage",
            "Scout whorls and spray at threshold", 4.4, "Pest / Disease"),
        Row("Frass visible in funnel", "Armyworm infestation",
            "Spray at threshold", 1.1, "Pest / Disease"),
        Row("Stunted plants, pale lower leaves", "Nitrogen shortfall",
            "Top dress with CAN", 9.0, "Nutrient / Vigor"),
        Row("Yellowing between veins", "Nitrogen shortfall, leaching after rain",
            "Top dress with CAN", 3.3, "Nutrient / Vigor"),
        Row("Bare patches in rows", "Poor germination, shallow planting depth",
            "Gap fill where stand is thin", 2.0, "Planting Gap"),
    ],
    "coffee / berry disease, Nyeri": [
        Row("Dark sunken lesions on green berries", "Coffee berry disease",
            "Copper spray on a 3 week cycle", 3.1, "Pest / Disease"),
        Row("Berry drop under canopy", "Coffee berry disease pressure",
            "Copper spray on a 3 week cycle", 2.4, "Pest / Disease"),
        Row("Orange pustules on leaf undersides", "Leaf rust",
            "Copper spray on a 3 week cycle", 1.8, "Pest / Disease"),
        Row("Weak flush, small leaves", "Low soil pH limiting uptake",
            "Lime and soil sample per block", 5.0, "Drainage / Soil"),
        Row("Thin canopy on the ridge", "Low soil pH, thin topsoil",
            "Lime and soil sample per block", 2.2, "Drainage / Soil"),
    ],
    "french beans / irrigation, Naivasha": [
        Row("Flower abortion mid block", "Moisture deficit at flowering",
            "Increase cycle length on block 4", 1.4, "Irrigation"),
        Row("Short internodes at line ends", "Emitter blockage reducing output",
            "Flush lines and replace emitters", 0.9, "Irrigation"),
        Row("Wilting by midday", "Moisture deficit, shallow rooting",
            "Increase cycle length on block 4", 0.6, "Irrigation"),
        Row("Dense broadleaf cover between rows", "Weed competition after rain",
            "Hand weed before pod set", 0.8, "Needs Investigation"),
    ],
    "avocado / an agronomist who writes tersely": [
        Row("Dieback", "Root rot", "Improve drainage, remove affected trees", 2.5),
        Row("Dieback on lower slope", "Root rot, waterlogging",
            "Improve drainage", 1.7),
        Row("Pale leaves", "Zinc deficiency", "Foliar feed", 3.0),
        Row("Pale leaves upper block", "Zinc deficiency", "Foliar feed", 1.2),
    ],
    "wheat / vocabulary this module has never met": [
        # Deliberately outside every list in aggregate.py.
        Row("Lodging across the headland", "Hail event on 12 March",
            "Assess insurance claim", 7.0, ""),
        Row("Flattened crop near the treeline", "Hail event on 12 March",
            "Assess insurance claim", 3.5, ""),
        Row("Tramline damage", "Machinery turning circle",
            "Adjust turning pattern next season", 0.9, ""),
    ],
    "mixed horticulture / one of everything": [
        Row("Chlorotic patches", "Nutrient deficiency", "Soil testing", 2.0),
        Row("Standing water after rain", "Poor drainage", "Open the drains", 1.5),
        Row("Thrips on flower buds", "Thrip infestation", "Spray at threshold", 0.7),
        Row("Nutsedge through the beds", "Weed pressure", "Weeding", 0.4),
        Row("Uneven emergence", "Seedbed too cloddy", "Improve seedbed prep", 1.1),
        Row("Discoloured patch", "Cause unclear", "Revisit next flight", 0.3),
    ],
}


def invariants(name, flight, rows, report):
    """The properties that must hold for any flight at all."""
    placed = [z for p in report["patterns"] for z in p["zones"]]
    chk(f"{name}: every annotation reaches a pattern",
        len(placed) == len(rows), f"{len(placed)} of {len(rows)}")
    chk(f"{name}: no annotation reaches two",
        len({z.id for z in placed}) == len(placed))
    chk(f"{name}: every pattern has a heading",
        all(p.get("heading", "").strip() for p in report["patterns"]))
    chk(f"{name}: every pattern has a sentence",
        all(len(p["sentence"]) > 20 for p in report["patterns"]),
        [p["sentence"] for p in report["patterns"]])
    chk(f"{name}: every pattern suggests something",
        all(len(p["suggestion"]) > 20 for p in report["patterns"]))

    unsourced = {p["heading"]: ag.unsourced_phrases(p) for p in report["patterns"]}
    chk(f"{name}: nothing invented",
        not any(unsourced.values()), {k: v for k, v in unsourced.items() if v})

    total = sum(z.area_acres or 0 for z in rows)
    summed = sum(p["acres"] for p in report["patterns"])
    chk(f"{name}: acres add up", abs(total - summed) < 0.001, (total, summed))
    chk(f"{name}: card totals match the patterns",
        abs(sum(c["acres"] for c in report["cards"]) - total) < 0.001)

    prose = " ".join([p["sentence"] for p in report["patterns"]]
                     + [p["suggestion"] for p in report["patterns"]]).lower()
    for word in ("you must", "you should", "immediately", "urgent", "critical",
                 "under pressure", "work through", "health score"):
        chk(f"{name}: voice holds ('{word}' absent)", word not in prose)

    pages = ag.paginate(report["groups"])
    rows_out = sum(len(b["rows"]) for page in pages for b in page)
    chk(f"{name}: pagination keeps every row", rows_out == len(rows),
        f"{rows_out} of {len(rows)}")


print("\n=== 1. flights from other crops, regions and habits ===")
for name, rows in FLIGHTS.items():
    print(f"\n-- {name}")
    flight = Flight(rows)
    report = ag.build(flight, rows)
    for p in report["patterns"]:
        print(f"     {p['heading']} · {p['count']} area(s) · ~{p['acres_text']} ac")
    invariants(name, flight, rows, report)

print("\n=== 2. the flights are read, not just accepted ===")
# Placing everything is easy if everything goes in one bucket. These check the
# layer actually separated what the agronomist separated.
maize = ag.build(Flight(FLIGHTS["maize / fall armyworm, Machakos"]),
                 FLIGHTS["maize / fall armyworm, Machakos"])
cats = {p["category"] for p in maize["patterns"]}
chk("maize: armyworm read as pest, not lumped with nutrition",
    "pest_disease" in cats and "soil_fertility" in cats, cats)
chk("maize: the three armyworm areas are one pattern",
    any(p["category"] == "pest_disease" and p["count"] == 3
        for p in maize["patterns"]),
    [(p["category"], p["count"]) for p in maize["patterns"]])

coffee = ag.build(Flight(FLIGHTS["coffee / berry disease, Nyeri"]),
                  FLIGHTS["coffee / berry disease, Nyeri"])
chk("coffee: berry disease and rust are pest/disease",
    any(p["category"] == "pest_disease" and p["count"] == 3
        for p in coffee["patterns"]),
    [(p["category"], p["count"]) for p in coffee["patterns"]])
chk("coffee: low pH read as soil fertility",
    any(p["category"] == "soil_fertility" for p in coffee["patterns"]),
    [p["category"] for p in coffee["patterns"]])

beans = ag.build(Flight(FLIGHTS["french beans / irrigation, Naivasha"]),
                 FLIGHTS["french beans / irrigation, Naivasha"])
chk("beans: emitter blockage read as irrigation",
    any(p["category"] == "irrigation" for p in beans["patterns"]),
    [p["category"] for p in beans["patterns"]])
chk("beans: the weed area is not swept into irrigation",
    any(p["category"] == "weeds" for p in beans["patterns"]),
    [p["category"] for p in beans["patterns"]])

avo = ag.build(Flight(FLIGHTS["avocado / an agronomist who writes tersely"]),
               FLIGHTS["avocado / an agronomist who writes tersely"])
chk("avocado: two-word annotations still separate rot from deficiency",
    len({p["category"] for p in avo["patterns"]}) >= 2,
    [(p["heading"], p["count"]) for p in avo["patterns"]])

print("\n=== 3. a flight in vocabulary the module has never seen ===")
hail = FLIGHTS["wheat / vocabulary this module has never met"]
wheat = ag.build(Flight(hail), hail)
chk("nothing is dropped", sum(p["count"] for p in wheat["patterns"]) == 3)
headings = [p["heading"] for p in wheat["patterns"]]
chk("the heading names what was seen, not just 'needs investigation'",
    any(h.lower() != "needs investigation" and len(h) > len("needs investigation")
        for h in headings), headings)
chk("the agronomist's own cause reaches the heading",
    any("hail" in h.lower() for h in headings), headings)
chk("the two hail areas are one pattern, the machinery one is not",
    any(p["count"] == 2 for p in wheat["patterns"])
    and any(p["count"] == 1 for p in wheat["patterns"]),
    [(p["heading"], p["count"]) for p in wheat["patterns"]])
invariants("hail flight", Flight(hail), hail, wheat)

print("\n=== 4. categories nobody here invented ===")
custom = [
    Row("Leaf scorch", "Fungal infection spreading from the boundary",
        "Fungicide programme", 2.0, "Fungal disease"),
    Row("Patchy colour", "Water management on the slope",
        "Review furrow layout", 1.0, "Water management"),
    Row("Thin stand", "Establishment failure", "Replant the gaps", 0.5, "Crop stand"),
]
c = ag.build(Flight(custom), custom)
placed = {p["category"] for p in c["patterns"]}
chk("a custom 'Fungal disease' label is read as pest/disease",
    "pest_disease" in placed, placed)
chk("a custom 'Water management' label is read as irrigation",
    "irrigation" in placed, placed)
chk("a custom 'Crop stand' label is read as establishment",
    "crop_establishment" in placed, placed)

print("\n=== 5. flights designed to break it ===")
cases = {
    "no findings at all": [],
    "a single finding": [Row("Poor vigour", "Soil condition", "Soil testing", 1.0)],
    "no text anywhere": [Row("", "", "", 1.0), Row("", "", "", 2.0)],
    "one word each": [Row("Weeds", "Weeds", "Weeding", 0.5),
                      Row("Dry", "Dry", "Water", 0.5)],
    "identical text fifteen times": [
        Row("Poor vigour", "Soil condition", "Soil testing", 1.0) for _ in range(15)],
    "no areas recorded": [
        Row("Poor vigour", "Soil condition", "Soil testing", None),
        Row("Weeds", "Weed pressure", "Weeding", None)],
    "negative areas": [Row("Poor vigour", "Soil condition", "Soil testing", -3.0)],
    "very long text": [
        Row("Observation " * 60, "Cause " * 60, "Recommendation " * 60, 2.0)],
    "punctuation only": [Row("---", "///", "...", 1.0)],
    "non-latin text": [Row("Magugu mengi shambani", "Magugu", "Palilia", 1.0)],
    "numbers as text": [Row("12345", "67890", "000", 1.0)],
}
for label, rows in cases.items():
    try:
        rep = ag.build(Flight(rows), rows)
        pages = ag.paginate(rep["groups"])
        kept = sum(len(b["rows"]) for page in pages for b in page)
        ok = (kept == len(rows)
              and all(p.get("heading") for p in rep["patterns"])
              and not any(ag.unsourced_phrases(p) for p in rep["patterns"]))
        chk(f"survives: {label}", ok,
            f"kept={kept} of {len(rows)}")
    except Exception as exc:                                  # noqa: BLE001
        chk(f"survives: {label}", False, f"{type(exc).__name__}: {exc}")

print("\n=== 6. a large randomly generated flight, twenty times over ===")
VOCAB = [
    ("Poor vigour", "Soil fertility", "Soil testing"),
    ("Wilting", "Water stress", "Adjust irrigation"),
    ("Leaf spots", "Fungal infection", "Fungicide"),
    ("Weeds through the rows", "Weed pressure", "Weeding"),
    ("Gaps in the stand", "Poor germination", "Gap fill"),
    ("Odd colour", "Cause unclear", "Revisit next flight"),
    ("Lodging", "Storm damage", "Assess and record"),
    ("Chewed margins", "Caterpillar feeding", "Scout and spray"),
]
random.seed(20260815)
worst = None
for trial in range(20):
    rows = []
    for _ in range(random.randint(1, 120)):
        o, c, r = random.choice(VOCAB)
        suffix = random.choice(["", " on the ridge", " near the dam", " block 3"])
        rows.append(Row(o + suffix, c, r, round(random.uniform(0.01, 8.0), 2)))
    rep = ag.build(Flight(rows), rows)
    kept = sum(len(b["rows"]) for page in ag.paginate(rep["groups"]) for b in page)
    ok = (kept == len(rows)
          and sum(p["count"] for p in rep["patterns"]) == len(rows)
          and len(rep["patterns"]) <= ag.MAX_PATTERNS
          and len(rep["observations"]) <= ag.MAX_OBSERVATIONS
          and all(p.get("heading") for p in rep["patterns"])
          and not any(ag.unsourced_phrases(p) for p in rep["patterns"]))
    if not ok and worst is None:
        worst = (trial, len(rows), kept, len(rep["patterns"]))
chk("twenty random flights, 1 to 120 findings each, all intact",
    worst is None, worst)

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
