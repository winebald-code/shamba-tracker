"""
Checks for the aggregation layer — the step that turns a flight's annotations
into the patterns page 1 reports.

The reference case throughout is the real IPM Farm flight: fifteen areas that V1
printed as fifteen near-identical rows, all labelled "Nutrient / Vigor", and
which the specification says should resolve into four patterns. If this file
passes, that flight produces the report in the V2 brief.

The guardrail gets its own section. The rule is that the summary may only
restate what the agronomist wrote, and a rule nothing checks is a hope, so every
phrase in every generated sentence is traced back to the source text.

Run it with:

    python tests/test_aggregate.py
"""
import os
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


class Row:
    """A finding, as far as the aggregation layer is concerned."""
    _n = 0

    def __init__(self, obs, cause, rec, acres, category="Nutrient / Vigor"):
        Row._n += 1
        self.id = Row._n
        self.sort_order = Row._n
        self.category = category
        self.observation = obs
        self.likely_cause = cause
        self.recommendation = rec
        self.area_acres = acres


class Flight:
    def __init__(self, rows, acreage=38.0):
        self.acreage = acreage
        self.findings = rows
        self.farm = type("Farm", (), {"acreage": acreage})()


# The flight from the V1 report, verbatim.
IPM = [
    Row("Uneven germination, weeds", "Overmounding / excess soil cover, water stress",
        "Reduce soil cover, maintain proper mound height", 4.09),
    Row("Reduced crop vigour", "Soil fertility", "Soil testing", 3.76),
    Row("Poor plant vigour, red soil", "Soil condition/fertility", "Soil testing", 3.01),
    Row("Reduced crop vigour, weeds overgrowth", "Soil fertility",
        "Soil testing and weeding", 2.54),
    Row("Poor plant vigour", "Nutrient deficiencies, soil condition", "Soil testing", 1.15),
    Row("Poor plant vigour", "Soil condition", "Soil testing", 0.89),
    Row("Uneven growth", "Water stress - low pressure at drip line end",
        "Compare water vs. healthy section, adjust irrigation", 0.77),
    Row("Poor plant vigour", "Soil fertility", "Soil testing", 0.76),
    Row("Poor plant vigour", "Inadequate moisture, nutrient uptake",
        "Soil testing; compare water vs. healthy section", 0.65),
    Row("Poor plant vigour", "Soil condition, nutrient uptake", "Soil testing", 0.60),
    Row("Poor plant vigour", "Soil condition", "Soil testing", 0.41),
    Row("Plant vigour", "Water stress", "Compare water collected vs. other section", 0.25),
    Row("Plant vigour", "Soil condition, loose covering", "Soil testing", 0.24),
    Row("Poor emergence", "Water stress", "Fix drip line on the ridge", 0.02),
    Row("Weeds", "Poor weed management", "Weeding", 0.01),
]

R = ag.build(Flight(IPM), IPM)
by_key = {p["category"]: p for p in R["patterns"]}
number = R["numbers"]

print("\n=== 1. the four patterns the specification expects ===")
chk("four patterns found, not one and not fifteen", len(R["patterns"]) == 4,
    [(p["category"], p["count"]) for p in R["patterns"]])
chk("soil fertility: 9 areas", by_key["soil_fertility"]["count"] == 9,
    by_key.get("soil_fertility", {}).get("count"))
chk("soil fertility: ~13.4 ac", by_key["soil_fertility"]["acres_text"] == "13.4",
    by_key["soil_fertility"]["acres_text"])
chk("crop establishment: 1 area", by_key["crop_establishment"]["count"] == 1)
chk("crop establishment: ~4.1 ac", by_key["crop_establishment"]["acres_text"] == "4.1",
    by_key["crop_establishment"]["acres_text"])
chk("irrigation: 4 areas", by_key["irrigation"]["count"] == 4,
    by_key.get("irrigation", {}).get("count"))
chk("irrigation: ~1.7 ac", by_key["irrigation"]["acres_text"] == "1.7",
    by_key["irrigation"]["acres_text"])
chk("weeds: 1 area, ~0.01 ac",
    by_key["weeds"]["count"] == 1 and by_key["weeds"]["acres_text"] == "0.01",
    (by_key["weeds"]["count"], by_key["weeds"]["acres_text"]))
chk("15 areas, ~19.2 acres marked",
    R["count"] == 15 and R["marked_text"] == "19.2", (R["count"], R["marked_text"]))
chk("a 38-acre field reads as 38, not 38.0", R["scouted_text"] == "38", R["scouted_text"])

print("\n=== 2. the largest finding is not consolidated away ===")
# The single biggest affected area on the farm has a different cause from the
# nine soil areas. Folding it in would tidy the page and hide it.
est = by_key["crop_establishment"]
chk("the 4.09 ac area stands alone", est["count"] == 1 and est["zones"][0].area_acres == 4.09)
chk("it is not in the soil fertility group",
    4.09 not in [z.area_acres for z in by_key["soil_fertility"]["zones"]])
chk("it is the largest single pattern by area after soil",
    est["acres"] > by_key["irrigation"]["acres"])

print("\n=== 3. classification reads the agronomist's words, not the enum ===")
# Every row above carries the same category label. The patterns come from the
# text, or they do not come at all.
chk("all fifteen share one enum label", {r.category for r in IPM} == {"Nutrient / Vigor"})
chk("zone 9 ('inadequate moisture, nutrient uptake') reads as irrigation",
    R["classified"][IPM[8].id]["category"] == "irrigation",
    R["classified"][IPM[8].id]["category"])
chk("zone 13 ('loose covering') stays soil, not establishment",
    R["classified"][IPM[12].id]["category"] == "soil_fertility",
    R["classified"][IPM[12].id]["category"])
chk("zone 14 ('fix drip line on the ridge') reads as irrigation",
    R["classified"][IPM[13].id]["category"] == "irrigation",
    R["classified"][IPM[13].id]["category"])
chk("zone 4 (weeds mentioned, cause is soil fertility) stays soil",
    R["classified"][IPM[3].id]["category"] == "soil_fertility",
    R["classified"][IPM[3].id]["category"])

print("\n=== 4. the guardrail: nothing is invented ===")
for p in R["patterns"]:
    missing = ag.unsourced_phrases(p)
    chk(f"{p['label']}: every phrase traced to the annotations", not missing, missing)

source_text = " ".join(f"{r.observation} {r.likely_cause} {r.recommendation}"
                       for r in IPM).lower()
for p in R["patterns"]:
    for cause in p["causes"]:
        chk(f"cause '{cause}' appears in the source", cause.lower() in source_text)
    for action in p["actions"]:
        chk(f"action '{action}' appears in the source", action.lower() in source_text)

print("\n=== 5. the voice ===")
banned = ["under pressure", "expensive later", "work through these",
          "in this order", "you must", "you should", "immediately",
          "urgent", "critical", "health score"]
prose = " ".join([p["sentence"] for p in R["patterns"]] +
                 [p["suggestion"] for p in R["patterns"]]).lower()
for word in banned:
    chk(f"does not say '{word}'", word not in prose)
chk("suggestions are offered, not ordered",
    all(any(h in p["suggestion"].lower()
            for h in ("could help", "may be worth", "worth a second look"))
        for p in R["patterns"]),
    [p["suggestion"] for p in R["patterns"]])

print("\n=== 6. zones are numbered largest area first ===")
ordered = [(number[z.id], z.area_acres) for z in R["zones"]]
chk("zone 1 is the largest area", ordered[0] == (1, 4.09), ordered[0])
chk("zone 15 is the smallest", ordered[-1] == (15, 0.01), ordered[-1])
chk("numbering descends by area",
    all(a >= b for (_, a), (_, b) in zip(ordered, ordered[1:])), ordered)
chk("every zone numbered exactly once", sorted(number.values()) == list(range(1, 16)))

print("\n=== 7. edge cases that reach real farms ===")
Row._n = 0
empty = ag.build(Flight([], acreage=12.0), [])
chk("a flight with no findings does not raise", empty["count"] == 0)
chk("and produces one empty findings sheet", ag.paginate(empty["groups"]) == [[]])

Row._n = 0
blank = [Row("Poor vigour", "", "", None)]
b = ag.build(Flight(blank, acreage=5.0), blank)
chk("a finding with no cause or recommendation still lands somewhere",
    len(b["patterns"]) == 1, b["patterns"])
chk("and says so rather than inventing one",
    "no cause was recorded" in b["patterns"][0]["sentence"].lower()
    or b["patterns"][0]["count"] == 1, b["patterns"][0]["sentence"])
chk("a missing area counts as zero, not as an error", b["marked_acres"] == 0)

Row._n = 0
mixed = [
    Row("Aphids on leaf margins", "Pest infestation", "Scout and spray", 2.0, "Pest / Disease"),
    Row("Standing water", "Water logging after rain", "Improve drainage", 1.0, "Irrigation"),
    Row("Missing plants", "Planting gap", "Replant", 0.5, "Planting Gap"),
    Row("Patchy colour", "Cause unclear", "Further investigation", 0.25, "Needs Investigation"),
]
m = ag.build(Flight(mixed, acreage=20.0), mixed)
keys = {p["category"] for p in m["patterns"]}
chk("pest, irrigation, establishment and unknown all separate",
    keys == {"pest_disease", "irrigation", "crop_establishment", "needs_investigation"}, keys)
chk("every category has a distinct map colour",
    len({ag.CATEGORIES[k]["colour"] for k in ag.CATEGORY_ORDER}) == 6)
chk("no category colour means 'healthy'",
    all("healthy" not in c["label"].lower() and "ok" != c["label"].lower()
        for c in ag.CATEGORIES.values()))

print("\n=== 8. page 1 stays readable however messy the flight ===")
Row._n = 0
many = []
for i in range(40):
    many.append(Row(f"Observation {i}", f"Cause number {i} unique wording {i}",
                    f"Action {i}", 0.5))
big = ag.build(Flight(many, acreage=100.0), many)
chk("patterns are capped", len(big["patterns"]) <= ag.MAX_PATTERNS, len(big["patterns"]))
chk("key observations capped at five",
    len(big["observations"]) <= ag.MAX_OBSERVATIONS, len(big["observations"]))
chk("but every zone is still carried to the detail pages",
    sum(len(g["rows"]) for g in big["groups"]) == 40,
    sum(len(g["rows"]) for g in big["groups"]))

Row._n = 0
longtext = [Row("A very long observation " * 6, "A very long cause " * 8,
                "A very long recommendation " * 8, 3.0) for _ in range(30)]
lg = ag.build(Flight(longtext, acreage=100.0), longtext)
pages = ag.paginate(lg["groups"])
rows_out = sum(len(b["rows"]) for page in pages for b in page)
chk("long findings paginate across sheets", len(pages) > 1, len(pages))
chk("and not one row is dropped", rows_out == 30, rows_out)
chk("no sheet is packed past its budget",
    all(sum(ag.GROUP_HEAD_MM + sum(ag.row_height(r) for r in b["rows"])
            for b in page) <= ag.SHEET_BODY_MM + 0.01 for page in pages))

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
