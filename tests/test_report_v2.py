"""
Checks for the V2 report and the security headers.

The V2 report replaces V1's per-zone list with the few patterns actually present
in a flight's annotations. This drives the whole path against the real IPM Farm
data, because that flight is the worked example the V2 brief was written from —
if the clustering is right for it, the numbers on page 1 match a report a person
has already checked by hand.

It also asserts the phrasing the brief rules out. Tone is not decoration here:
"under pressure" and "work through these, in this order" are the specific lines
that prompted V2.

    python tests/test_report_v2.py
"""
import os,sys,tempfile,shutil,io,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"v2.db"),SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, pdf_gen
from models import db,Farm,Flight,Finding
from datetime import date
from flask import render_template
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)

IPM=[(4.09,"Uneven germination, weeds","Overmounding / excess soil cover, water stress","Reduce soil cover, maintain proper mound height"),
 (3.76,"Reduced crop vigour","Soil fertility","Soil testing"),
 (3.01,"Poor plant vigour, red soil","Soil condition/fertility","Soil testing"),
 (2.54,"Reduced crop vigour, weeds overgrowth","Soil fertility","Soil testing and weeding"),
 (1.15,"Poor plant vigour","Nutrient deficiencies, soil condition","Soil testing"),
 (0.89,"Poor plant vigour","Soil condition","Soil testing"),
 (0.77,"Uneven growth","Water stress — low pressure at drip line end","Compare water vs. healthy section, adjust irrigation"),
 (0.76,"Poor plant vigour","Soil fertility","Soil testing"),
 (0.65,"Poor plant vigour","Inadequate moisture, nutrient uptake","Soil testing; compare water vs. healthy section"),
 (0.60,"Poor plant vigour","Soil condition, nutrient uptake","Soil testing"),
 (0.41,"Poor plant vigour","Soil condition","Soil testing"),
 (0.25,"Plant vigour","Water stress","Compare water collected vs. other section"),
 (0.24,"Plant vigour","Soil condition, loose covering","Soil testing"),
 (0.02,"Poor emergence","Water stress","Fix drip line on the ridge"),
 (0.01,"Weeds","Poor weed management","Weeding")]

with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0,farmer_name="J. Mwangi",location="Nakuru")
    db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=6,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,12)); db.session.add(fl); db.session.commit()
    for i,(ac,o,c,r) in enumerate(IPM):
        db.session.add(Finding(flight_id=fl.id,category="Nutrient / Vigor",colour_meaning="Needs testing",
            colour_swatch="#D64550",observation=o,likely_cause=c,recommendation=r,
            area_text=f"{ac} ac",area_acres=ac,sort_order=i))
    db.session.commit()
    shutil.copy("samples/sample_annotated_map.jpg", os.path.join(appmod.UPLOAD_DIR,"ipm.jpg"))
    fl.map_image="ipm.jpg"; db.session.commit()
    FLID=fl.id; TOK=fl.share_token

c=appmod.app.test_client()
c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

print("=== security headers ===")
r=c.get("/")
for h,frag in [("Strict-Transport-Security",None),("Content-Security-Policy","frame-ancestors 'none'"),
               ("X-Frame-Options","DENY"),("X-Content-Type-Options","nosniff"),
               ("Referrer-Policy","strict-origin"),("Permissions-Policy","geolocation=()"),
               ("Cross-Origin-Opener-Policy","same-origin"),("Cross-Origin-Resource-Policy","same-origin")]:
    v=r.headers.get(h)
    if h=="Strict-Transport-Security":
        chk("HSTS withheld over plain HTTP", v is None, v)
    else:
        chk(f"{h} present", v and (frag in v), v)
r2=c.get("/",headers={"X-Forwarded-Proto":"https"})
chk("HSTS sent behind an HTTPS proxy","max-age=63072000" in (r2.headers.get("Strict-Transport-Security") or ""),
    r2.headers.get("Strict-Transport-Security"))

print("\n=== V2 report ===")
r=c.post(f"/flights/{FLID}/generate",follow_redirects=True)
chk("generates",r.status_code==200,r.status_code)
page=c.get(f"/flights/{FLID}/report").data.decode()
chk("three sheets on screen",len(re.findall(r'<section class="sheet"',page))==3,len(re.findall(r'<section class="sheet"',page)))
chk("summary heading present","Field scouting summary" in page)
chk("categories present","Issue categories" in page)
chk("observations present","Key observations" in page)
chk("suggestions present","Suggested areas to investigate" in page)
chk("detail page present","Detailed findings" in page)
chk("all 15 zones still listed",len(re.findall(r'data-l="Zone"',page))==15,len(re.findall(r'data-l="Zone"',page)))
print("\n=== tone: banned V1 phrasing is gone ===")
for bad in ["under pressure","small now, expensive later","Work through these","field health score",
            "in this order","Needs testing"]:
    chk(f"absent: {bad!r}", bad.lower() not in page.lower())

pdf=c.get(f"/flights/{FLID}/report.pdf")
chk("PDF returned",pdf.data[:5]==b"%PDF-",pdf.data[:12])
open(os.path.join(TMP,"v2.pdf"),"wb").write(pdf.data)
import subprocess
pages=int([l for l in subprocess.run(["pdfinfo",os.path.join(TMP,"v2.pdf")],capture_output=True,text=True).stdout.split("\n") if l.startswith("Pages")][0].split()[-1])
chk("exactly 3 PDF pages",pages==3,pages)
chk("public link works",appmod.app.test_client().get(f"/r/{TOK}").status_code==200)
print("\n=== 5. a second real flight groups by what the cause says ===")
# The IPM flight above is the brief's worked example. This one is a later flight
# whose six findings had all been filed under one category, and it is here
# because it caught three faults the first dataset could not: "nitrogen" and
# "sprinkler" were in no keyword list, and a pest finding that mentioned the
# weedy edge the aphids came from was filed under Weeds.
class _F:
    def __init__(self, i, acres, obs, cause, rec):
        self.id, self.area_acres = i, acres
        self.observation, self.likely_cause, self.recommendation = obs, cause, rec
        self.category = "Pest / Disease"          # as they were all stored

FLIGHT2 = [
    _F(1, 8.08, "Reduced growth along one irrigation line.",
       "Suspected malfunction in the drip line along that row.",
       "Inspect and test the drip line for blockages or damage."),
    _F(2, 3.09, "Scattered dark leaf spotting on the older, lower leaves.",
       "Early-stage fungal leaf spot, still limited to the lower canopy.",
       "Begin a preventive spray programme."),
    _F(3, 2.82, "Pale, uneven canopy colour through the middle of the block.",
       "Nitrogen running short as the crop reaches peak demand.",
       "Take a leaf-tissue sample to confirm, then top-dress nitrogen."),
    _F(4, 2.04, "A cluster of stunted plants with curled leaves near the track.",
       "Aphid pressure carrying virus in from the weedy field edge.",
       "Scout under the leaves and spot-spray if aphids are present."),
    _F(5, 1.81, "A dry, lighter wedge fanning out from one corner of the field.",
       "A blocked or mis-aligned sprinkler leaving this wedge short of water.",
       "Check the sprinklers on that span and clear the blocked nozzle."),
    _F(6, 0.23, "A small, bright, vigorous patch greener than the crop around it.",
       "Healthy new growth where the replanted section has established well.",
       "No action needed, use this patch as the benchmark."),
]
import aggregation as _agg
_a = _agg.aggregate(FLIGHT2, {f.id: f.id for f in FLIGHT2})
_by = {g["name"]: (g["count"], g["zones"]) for g in _a["groups"]}
for _name, _cnt, _zones in [("Irrigation / Moisture", 2, [1, 5]),
                            ("Pest / Disease", 2, [2, 4]),
                            ("Soil Fertility / Nutrition", 1, [3]),
                            ("Crop Establishment", 1, [6])]:
    chk(f"{_name}: {_cnt} area(s), zones {_zones}",
        _by.get(_name) == (_cnt, _zones), _by.get(_name))
chk("a nitrogen cause is not a pest problem",
    _agg.classify(FLIGHT2[2]) == "Soil Fertility / Nutrition", _agg.classify(FLIGHT2[2]))
chk("a sprinkler cause is an irrigation problem",
    _agg.classify(FLIGHT2[4]) == "Irrigation / Moisture", _agg.classify(FLIGHT2[4]))
chk("aphids beat the weedy edge they came from",
    _agg.classify(FLIGHT2[3]) == "Pest / Disease", _agg.classify(FLIGHT2[3]))
chk("'ph' no longer matches inside 'aphid'",
    "ph" not in dict(_agg.CAUSE_PATTERNS)["Soil Fertility / Nutrition"])
chk("the acreage still totals what the header claims",
    _a["total_acres_text"] == "18.1", _a["total_acres_text"])
chk("the largest pattern leads", _a["groups"][0]["name"] == "Irrigation / Moisture",
    _a["groups"][0]["name"])
chk("suggestions separate distinct recommendations",
    "; " in _a["groups"][0]["suggestion"], _a["groups"][0]["suggestion"][:70])

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
