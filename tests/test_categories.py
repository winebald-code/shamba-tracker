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
legend=re.findall(r'<span class="lb">([^<]+)</span>', rep)
chk("legend lists the same six names", sorted(legend)==sorted(parsing.CATEGORIES), legend)
with appmod.app.app_context():
    used=sorted({f.category for f in db.session.get(Flight,FLID).findings})
for u in used:
    chk(f"'{u}' from the review page appears on the report", u in rep)

print("\n=== 6. the IPM clustering is unaffected ===")
class Fk:
    def __init__(s,i,a,o,c_,r,cat="Nutrient / Vigor"):
        s.id=i;s.area_acres=a;s.observation=o;s.likely_cause=c_;s.recommendation=r;s.category=cat
rows=[Fk(1,4.09,"Uneven germination, weeds","Overmounding / excess soil cover, water stress","Reduce soil cover"),
      Fk(2,3.76,"Reduced crop vigour","Soil fertility","Soil testing"),
      Fk(3,3.01,"Poor plant vigour, red soil","Soil condition/fertility","Soil testing"),
      Fk(4,2.54,"Reduced crop vigour, weeds overgrowth","Soil fertility","Soil testing and weeding"),
      Fk(5,1.15,"Poor plant vigour","Nutrient deficiencies, soil condition","Soil testing"),
      Fk(6,0.89,"Poor plant vigour","Soil condition","Soil testing"),
      Fk(7,0.77,"Uneven growth","Water stress — low pressure at drip line end","Adjust irrigation"),
      Fk(8,0.76,"Poor plant vigour","Soil fertility","Soil testing"),
      Fk(9,0.65,"Poor plant vigour","Inadequate moisture, nutrient uptake","Soil testing"),
      Fk(10,0.60,"Poor plant vigour","Soil condition, nutrient uptake","Soil testing"),
      Fk(11,0.41,"Poor plant vigour","Soil condition","Soil testing"),
      Fk(12,0.25,"Plant vigour","Water stress","Compare water collected"),
      Fk(13,0.24,"Plant vigour","Soil condition, loose covering","Soil testing"),
      Fk(14,0.02,"Poor emergence","Water stress","Fix drip line"),
      Fk(15,0.01,"Weeds","Poor weed management","Weeding")]
a=aggregation.aggregate(rows,{f.id:f.id for f in rows})
got={g["name"]:(g["count"],round(g["acres"],2)) for g in a["groups"]}
for name,(cnt,ac) in {"Soil Fertility / Nutrition":(9,13.36),"Crop Establishment":(1,4.09),
                      "Irrigation / Moisture":(4,1.69),"Weeds":(1,0.01)}.items():
    chk(f"{name}: {cnt} zones", got.get(name)==(cnt,ac), got.get(name))
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
