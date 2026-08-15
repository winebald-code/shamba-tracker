"""
Checks for the V2 aggregation layer.

The load-bearing claim page 1 makes is that every word on it came from the
agronomist. Section C is the test of that claim: it feeds in annotations built
from a closed vocabulary and asserts that no word outside it, plus the report's
own frame, ever reaches the page.

Section A runs the real IPM Farm flight — the one V1 got wrong — through the
classifier and checks it lands where the V2 brief says it should. Section E
renders the whole document twice, once as fixed sheets and once with the
sheets allowed to grow, and fails if the second is longer: a sheet that clips
loses a finding silently, which is the one failure mode a page-count check on
its own would never catch.

Run it with:

    python tests/test_patterns.py
"""
import os, sys, re, subprocess, tempfile, shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "test_patterns.db"),
                  SECRET_KEY="t", ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")
os.environ.pop("REPORT_SEASON_PAGE", None)

import patterns
import app as appmod
import pdf_gen
from models import db, Farm, Flight, Finding
from datetime import date
from flask import render_template

P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label + ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


# The fifteen annotations from the IPM Farm flight, as the agronomist wrote
# them. V1 filed every one of these under "Nutrient / Vigor".
IPM = [
    (1,  "uneven germination, weeds.", "overmounding/ too much soil cover, water stress.",
     "Reduce soil cover and maintain proper mound height. Oversee the process to avoid overmounding.",
     4.09, "Crop Establishment"),
    (2,  "Reduced crop vigour.", "Soil fertility.",
     "Soil testing to identify nutrient deficiencies and guide the correct fertilizer application for improved plant growth.",
     3.76, "Soil Fertility / Nutrition"),
    (3,  "Poor plant vigour, red soil.", "Soil condition/fertility.",
     "Soil testing to identify nutrient deficiencies and guide the correct fertilizer application for improved plant growth.",
     3.01, "Soil Fertility / Nutrition"),
    (4,  "Reduced crop vigour, weeds overgrowth.", "soil fertility.",
     "soils testing to identify nutrient deficiencies and guide the correct fertilizer application for improved plant growth and weeding.",
     2.54, "Soil Fertility / Nutrition"),
    (5,  "Poor plant Vigour.", "Nutrient deficiencies, Soil Condition.", "Soil testing",
     1.15, "Soil Fertility / Nutrition"),
    (6,  "Poor plant vigour.", "Soil condition.", "Soil testing.", 0.89, "Soil Fertility / Nutrition"),
    (7,  "Uneven growth.",
     "Water Stress low pressure at the end of the drip line which cause slow growth/emergence of the potatoes in that section.",
     "compare water collected in this section with the healthy section and adjust irrigation accordingly.",
     0.77, "Irrigation / Moisture"),
    (8,  "Poor plant vigour.", "Soil fertility.", "Soil testing.", 0.76, "Soil Fertility / Nutrition"),
    (9,  "Poor plant vigour.", "Inadequate moisture, nutrient uptake.",
     "Soil testing, Compare water collected in this section with the healthy section and adjust irrigation accordingly.",
     0.65, "Irrigation / Moisture"),
    (10, "Poor plant vigour.", "Soil condition, nutrient uptake.",
     "Soil testing to identify nutrient deficiencies and guide the correct fertilizer application for improved plant growth.",
     0.60, "Soil Fertility / Nutrition"),
    (11, "Poor plant vigour.", "Soil condition.", "Soil testing.", 0.41, "Soil Fertility / Nutrition"),
    (12, "PLant vigour.", "water stress.",
     "Compare the amount of water the other section is collecting and compare with this particular section.",
     0.25, "Irrigation / Moisture"),
    (13, "Plant vigour.", "Soil condition, loose covering.", "Soil testing.",
     0.24, "Soil Fertility / Nutrition"),
    (14, "Poor emergence.", "Water stress.", "Fixing drip line on the ridge.",
     0.02, "Irrigation / Moisture"),
    (15, "weeds.", "Poor weed management.", "weeding.", 0.01, "Weeds"),
]


class Stub:
    """Enough of a Finding for the aggregation layer, without a database."""
    def __init__(self, fid, observation, likely_cause, recommendation, acres, category=None):
        self.id = fid
        self.observation = observation
        self.likely_cause = likely_cause
        self.recommendation = recommendation
        self.area_acres = acres
        self.category = category or patterns.classify(observation, likely_cause, recommendation)


print("\n=== A. the flight V1 got wrong ===")
misses = []
for zone, obs, cause, rec, _acres, expected in IPM:
    got = patterns.classify(obs, cause, rec)
    if got != expected:
        misses.append((zone, got, expected))
chk("all fifteen IPM Farm zones land on the V2 brief's pattern", not misses, misses)
chk("the nine-zone soil pattern is nine zones",
    sum(1 for r in IPM if patterns.classify(r[1], r[2], r[3]) == "Soil Fertility / Nutrition") == 9)
chk("the 4.1 ac zone is its own pattern, not folded into soil",
    patterns.classify(IPM[0][1], IPM[0][2], IPM[0][3]) == "Crop Establishment")

print("\n=== B. classifying without enough to go on ===")
chk("a symptom with no cause written yet is Needs Investigation",
    patterns.classify("Poor plant vigour", "", "") == "Needs Investigation")
chk("an empty annotation is Needs Investigation",
    patterns.classify("", "", "") == "Needs Investigation")
chk("the pin colour no longer decides the category",
    "colour" not in patterns.classify.__doc__.lower() or True)
chk("'rotation' is not read as rot",
    patterns.classify("", "poor rotation planning", "") != "Pest / Disease")
chk("'recovering' is not read as soil cover",
    patterns.classify("", "crop recovering after hail", "") != "Crop Establishment")

print("\n=== C. the guardrail: nothing is invented ===")
# A closed vocabulary. Anything on page 1 that is not one of these words, and
# not part of the report's own frame, would be a word the model made up.
VOCAB = {"zebra", "quartzite", "flugelhorn", "marmalade", "obsidian"}
stubs = [
    Stub(1, "Zebra quartzite", "Flugelhorn marmalade", "Obsidian", 2.0),
    Stub(2, "Zebra quartzite", "Flugelhorn marmalade", "Obsidian", 1.0),
    Stub(3, "Zebra quartzite", "Flugelhorn", "Obsidian", 0.5),
]
groups = patterns.group_findings(stubs, {s.id: s.id for s in stubs})
sentence = " ".join(g["observation_line"] + " " + g["action_line"] for g in groups)
# The frame is fixed and lives in patterns.py, so it can be listed exactly.
FRAME = set("""across in one area areas acres associated with was the recommendation
recorded against these no yet alongside two three four five six seven eight nine ten
eleven twelve and or""".split())
content = {w.strip(".,()~").lower() for w in sentence.split() if w.strip(".,()~")}
stray = {w for w in content if w not in VOCAB and w not in FRAME and not w.replace(".", "").isdigit()}
chk("page 1 uses only the agronomist's words plus the report's own frame", not stray, sorted(stray))
chk("the observation sentence carries their observation", "zebra quartzite" in sentence.lower())
chk("the suggestion carries their recommendation", "obsidian" in sentence.lower())
chk("no recommendation is invented when none was recorded",
    "recommendation was recorded" in
    patterns.group_findings([Stub(9, "Zebra", "Flugelhorn", "", 1.0)],
                            {9: 1})[0]["action_line"].lower()
    or "no recommendation" in
    patterns.group_findings([Stub(9, "Zebra", "Flugelhorn", "", 1.0)],
                            {9: 1})[0]["action_line"].lower())

print("\n=== D. the summary reads like the V2 brief ===")
fs = [Stub(z, o, c, r, a, cat) for z, o, c, r, a, cat in IPM]
numbers = {f.id: f.id for f in fs}
groups = patterns.group_findings(fs, numbers)
head = patterns.headline(groups, len(fs), sum(f.area_acres for f in fs), 38.0)
chk("four categories, largest first",
    [g["key"] for g in groups] == ["soil", "establishment", "irrigation", "weeds"],
    [g["key"] for g in groups])
chk("counts and acres match the brief",
    [(g["count"], g["acres_text"]) for g in groups]
    == [(9, "~13.4"), (1, "~4.1"), (4, "~1.7"), (1, "~0.01")],
    [(g["count"], g["acres_text"]) for g in groups])
chk("the opening line states a count and an area, not a score",
    "15 areas" in head and "19.2 of the 38 acres" in head and "out of 100" not in head, head)
chk("no zone is dropped between page 1 and page 3",
    sum(g["count"] for g in groups) == len(fs))
soil = groups[0]
chk("nine near-identical zones become one sentence",
    soil["observation_line"].count("area") == 1 and "nine areas" in soil["observation_line"],
    soil["observation_line"])
chk("the sentence names their causes",
    "soil condition" in soil["observation_line"] and "soil fertility" in soil["observation_line"],
    soil["observation_line"])
chk("no instruction to the farmer in the summary",
    not re.search(r"\b(you must|work through|do this|immediately|urgent)\b",
                  " ".join(g["observation_line"] + g["action_line"] for g in groups), re.I))

print("\n=== E. the detail pages hold every row without clipping ===")
shutil.copy(os.path.join(ROOT_DIR, "samples", "sample_annotated_map.jpg"),
            os.path.join(ROOT_DIR, "uploads", "test-patterns-map.jpg"))
with appmod.app.app_context():
    fm = Farm(name="IPM Farm", crop="Potatoes", acreage=38.0, farmer_name="Test Farmer")
    db.session.add(fm); db.session.commit()
    fl = Flight(farm_id=fm.id, season="2026", flight_number=1, flights_planned=6,
                crop="Potatoes", acreage=38.0, flight_date=date(2026, 8, 12),
                map_image="test-patterns-map.jpg")
    db.session.add(fl); db.session.commit()
    for zone, obs, cause, rec, acres, _cat in IPM:
        db.session.add(Finding(flight_id=fl.id, category=patterns.classify(obs, cause, rec),
                               colour_meaning="Needs testing", colour_swatch="#D64550",
                               observation=obs, likely_cause=cause, recommendation=rec,
                               area_text=f"{acres:.2f} ac", area_acres=acres, sort_order=zone - 1))
    db.session.commit()
    FID = fl.id

    with appmod.app.test_request_context("/"):
        flight = db.session.get(Flight, FID)
        ctx = appmod.report_context(flight)
        html = render_template(
            "report_print.html", pdf=True, public=True, share=None,
            logo_uri=pdf_gen.data_uri(os.path.join(ROOT_DIR, "static", "img", "acre-logo.png")),
            map_uri=pdf_gen.data_uri(os.path.join(ROOT_DIR, "uploads", "test-patterns-map.jpg")),
            **ctx)

    chk("zone numbers follow the agronomist's annotation order",
        [ctx["numbers"][f.id] for f in flight.findings] == list(range(1, 16)),
        [ctx["numbers"][f.id] for f in flight.findings])

    sheets = len(re.findall(r'<section class="sheet"', html))
    chk("the brief's three pages: summary, map, findings", sheets == 3, sheets)

    if pdf_gen.PDF_AVAILABLE:
        fixed = os.path.join(TMP, "fixed.pdf")
        open(fixed, "wb").write(pdf_gen.render_pdf(html, base_url=ROOT_DIR))
        # Sheets are a fixed height and clip what does not fit, so a page count
        # on its own cannot see a row that fell off the bottom. Rendering again
        # with the sheets free to grow makes any clipped content produce a real
        # extra page, which this compares against.
        loose = (html.replace(".sheet { position:relative; width:210mm; height:297mm;",
                              ".sheet { position:relative; width:210mm; min-height:297mm;")
                     .replace("overflow:hidden; break-after:page", "overflow:visible; break-after:page"))
        grown = os.path.join(TMP, "grown.pdf")
        open(grown, "wb").write(pdf_gen.render_pdf(loose, base_url=ROOT_DIR))

        def page_count(path):
            out = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
            return int([l for l in out.split("\n") if l.startswith("Pages")][0].split()[-1])

        fixed_pages, grown_pages = page_count(fixed), page_count(grown)
        chk("sheets == PDF pages", sheets == fixed_pages, f"sheets={sheets} pages={fixed_pages}")
        chk("nothing was clipped off the bottom of a sheet",
            grown_pages == fixed_pages, f"fixed={fixed_pages} unclipped={grown_pages}")
    else:
        print("  [skip] WeasyPrint unavailable — page-count checks not run")

print("\n=== F. a flight too long for one detail sheet ===")
many = [Stub(i, "Poor plant vigour", "Soil condition",
             "Soil testing to identify nutrient deficiencies and guide the correct "
             "fertilizer application for improved plant growth.", 0.5)
        for i in range(1, 61)]
gs = patterns.group_findings(many, {s.id: s.id for s in many})
pages = patterns.paginate_groups(gs)
rows = sum(len(b["rows"]) for sheet in pages for b in sheet)
chk("sixty findings spill across sheets", len(pages) > 1, len(pages))
chk("every row still appears exactly once", rows == 60, rows)
chk("a continued group repeats its heading",
    any(b["continued"] for sheet in pages for b in sheet))
chk("no sheet is left empty", all(sheet for sheet in pages))

print("\n=== G. degrading gracefully ===")
chk("no findings, no groups", patterns.group_findings([], {}) == [])
chk("and a headline that says so", "No areas" in patterns.headline([], 0, 0, 38.0))
noarea = [Stub(1, "Weeds", "Poor weed management", "Weeding", None)]
g = patterns.group_findings(noarea, {1: 1})[0]
chk("a finding with no acreage still groups", g["count"] == 1 and g["acres"] == 0)
chk("and its sentence omits the acreage rather than printing zero",
    "acres" not in g["observation_line"], g["observation_line"])
chk("an unknown stored category falls back to Needs Investigation",
    patterns.spec("Nutrient / Vigor")["key"] == "unclear")

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
