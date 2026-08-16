"""
Checks that one category list drives both the review page and the report.

These were two separate lists. A finding filed under "Nutrient / Vigor" on the
review page appeared on the farmer's map legend as "Soil Fertility /
Nutrition", so the words an agronomist chose were never the words the farmer
read. The two are now one list, and this asserts they cannot drift apart
again.

It also covers the migration for findings recorded under the old names, which
has to be idempotent because it runs on every start-up.

    python tests/test_categories.py
"""
import os,sys,tempfile,shutil,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"c.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, parsing, aggregation
from models import db,Farm,Flight,Finding
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)

print("=== 1. one list, not two ===")
chk("review categories == report legend", parsing.CATEGORIES == aggregation.CATEGORY_ORDER, parsing.CATEGORIES)
chk("every category has a legend colour",
    all(c in aggregation.CATEGORY_COLOURS for c in parsing.CATEGORIES))
chk("six categories", len(parsing.CATEGORIES)==6, len(parsing.CATEGORIES))

print("\n=== 2. legacy names migrate ===")
for old,new in [("Nutrient / Vigor","Soil Fertility / Nutrition"),("Irrigation","Irrigation / Moisture"),
                ("Planting Gap","Crop Establishment"),("Drainage / Soil","Soil Fertility / Nutrition")]:
    chk(f"{old} -> {new}", parsing.normalise_category(old)==new, parsing.normalise_category(old))

print("\n=== 3. the migration moves existing rows ===")
with appmod.app.app_context():
    fm=Farm(name="Legacy Farm",crop="Potatoes",acreage=38.0); db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=6,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,12)); db.session.add(fl); db.session.commit()
    # write the OLD names straight to the table, as a pre-merge database would hold
    for ac,cat in [(3.0,"Nutrient / Vigor"),(2.0,"Irrigation"),(1.0,"Planting Gap")]:
        db.session.add(Finding(flight_id=fl.id,category=cat,colour_meaning="Needs testing",
            observation="o",likely_cause="c",recommendation="r",area_acres=ac))
    db.session.commit(); FLID=fl.id
    import schema
    schema.migrate_categories(db)
    db.session.expire_all()
    cats=sorted(f.category for f in db.session.get(Flight,FLID).findings)
    chk("no legacy names remain", not any(c in parsing.LEGACY_CATEGORIES for c in cats), cats)
    chk("all now on the legend", all(c in parsing.CATEGORIES for c in cats), cats)
    # running it twice must change nothing further
    schema.migrate_categories(db); db.session.expire_all()
    chk("migration is idempotent", sorted(f.category for f in db.session.get(Flight,FLID).findings)==cats)

print("\n=== 4. the review page shows the legend colour ===")
c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
page=c.get(f"/flights/{FLID}").data.decode()
# Scope to the category select; the page also has a status dropdown.
sel=re.search(r'<select data-field="category".*?</select>', page, re.S)
opts=re.findall(r'<option value="([^"]+)"', sel.group() if sel else "")
chk("dropdown offers exactly the legend categories", sorted(set(opts))==sorted(parsing.CATEGORIES), sorted(set(opts)))
chk("a colour swatch sits beside the dropdown", 'title="Colour on the report map"' in page)
for name,colour in aggregation.REPORT_CATEGORIES[:3]:
    pass
chk("swatch uses a legend colour", any(f"background:{v}" in page for v in aggregation.CATEGORY_COLOURS.values()))

print("\n=== 5. review labels match the report legend exactly ===")
# The legend lives on the map page, which only renders when a map is uploaded.
with appmod.app.app_context():
    shutil.copy("samples/sample_annotated_map.jpg", os.path.join(appmod.UPLOAD_DIR,"c.jpg"))
    _f=db.session.get(Flight,FLID); _f.map_image="c.jpg"; db.session.commit()
rep=c.get(f"/flights/{FLID}/report").data.decode()
key=re.findall(r'<span class="lb">([^<]+)</span>', rep)
# The key names the categories this flight actually produced, and nothing else:
# a colour on it has to point at something on the map.
chk("the key names only categories in use", set(key) <= set(parsing.CATEGORIES), key)
chk("the key carries the report colours",
    all(f"background:{aggregation.CATEGORY_COLOURS[n]}" in rep for n in key), key)
with appmod.app.app_context():
    used=sorted({f.category for f in db.session.get(Flight,FLID).findings})
for u in used:
    chk(f"'{u}' from the review page appears on the report", u in rep)

print("\n=== 6. the reference flight still clusters as the brief describes ===")
# The real DroneDeploy export for this flight, so there is one dataset of record
# rather than a paraphrase that can drift from it.
import parsing as _parsing
class _Row:
    def __init__(self, i, d):
        self.id = i
        self.area_acres = d["area_acres"]
        self.observation = d["observation"]
        self.likely_cause = d["likely_cause"]
        self.recommendation = d["recommendation"]
        self.category = d["category"]
_IPM = [_Row(i + 1, d) for i, d in enumerate(_parsing.parse_csv(
    open(os.path.join(ROOT, "samples", "ipm_flight1_dronedeploy_export.csv"), "rb").read()))]
_agg = aggregation.aggregate(_IPM, {r.id: r.id for r in _IPM})
_got = {g["name"]: (g["count"], g["acres_text"]) for g in _agg["groups"]}
for _name, _cnt, _ac in [("Soil Fertility / Nutrition", 9, "13.4"),
                         ("Crop Establishment", 1, "4.1"),
                         ("Irrigation / Moisture", 4, "1.7"),
                         ("Weeds", 1, "0.01")]:
    chk(f"{_name}: {_cnt} areas ~{_ac} ac", _got.get(_name) == (_cnt, _ac), _got.get(_name))

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
