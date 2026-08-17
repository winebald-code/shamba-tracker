"""
Checks the map link, the season page and the zone numbering.

Three things that have to agree with something outside the report: the link
a farmer taps has to be the one on the farm now, a season page only means
anything once there is a flight to compare against, and the zone numbers have
to be the numbers printed on the aerial image.

That last one is the reason this file exists. The numbers were assigned by a
sort of the findings rather than by the order the areas were annotated in, so
the key said "zone 5" while the image said something else, and there was
nothing in either to show they disagreed.

    python tests/test_map_and_season.py
"""
import os,sys,tempfile,shutil,io,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"t3.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, parsing
from models import db,Farm,Flight,Finding
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)
CSV=open(os.path.join(ROOT,"samples","ipm_flight2_dronedeploy_export.csv"),"rb").read()
c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
OLD="https://dronedeploy.com/old"; NEW="https://dronedeploy.com/new"
with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0,dronedeploy_project_url=OLD)
    db.session.add(fm); db.session.commit(); FMID=fm.id
r=c.post(f"/farms/{FMID}/flights/new",data={"season":"2026","flight_number":"1","flights_planned":"6",
    "crop":"Potatoes","acreage":"38","flight_date":"2026-08-12"},follow_redirects=True)
with appmod.app.app_context():
    fl=Flight.query.filter_by(farm_id=FMID).first(); FLID=fl.id
c.post(f"/flights/{FLID}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},content_type="multipart/form-data",follow_redirects=True)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    for f in fl.findings:
        if not (f.likely_cause or "").strip(): f.likely_cause="Water stress"
    shutil.copy(os.path.join(ROOT,"samples","sample_annotated_map.jpg"),os.path.join(appmod.UPLOAD_DIR,"m.jpg"))
    fl.map_image="m.jpg"; db.session.commit()

print("=== 1. the map link follows the farm ===")
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("a new flight inherits the farm's link", OLD in rep)
c.post(f"/farms/{FMID}/edit",data={"name":"IPM Farm","crop":"Potatoes","acreage":"38",
    "dronedeploy_project_url":NEW},follow_redirects=True)
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("updating the farm reaches the report", NEW in rep)
chk("the old link is gone", OLD not in rep)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID); fl.dronedeploy_project_url="https://dronedeploy.com/own"; db.session.commit()
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("a flight's own link still wins", "dronedeploy.com/own" in rep and NEW not in rep)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID); fl.dronedeploy_project_url=""; db.session.commit()

print("\n=== 2. no season page until there is a comparison ===")
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("flight 1 alone has no season page", "Season to date" not in rep)
chk("and is three pages", len(re.findall(r'<section class="sheet"',rep))==3,
    len(re.findall(r'<section class="sheet"',rep)))
with appmod.app.app_context():
    fm=db.session.get(Farm,FMID)
    f2=Flight(farm_id=FMID,season="2026",flight_number=2,flights_planned=6,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,17)); db.session.add(f2); db.session.commit(); F2=f2.id
c.post(f"/flights/{F2}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},content_type="multipart/form-data",follow_redirects=True)
with appmod.app.app_context():
    f2=db.session.get(Flight,F2)
    for f in f2.findings:
        if not (f.likely_cause or "").strip(): f.likely_cause="Water stress"
    f2.map_image="m.jpg"; db.session.commit()
rep1=c.get(f"/flights/{FLID}/report").data.decode()
chk("flight 1 still has none once flight 2 exists", "Season to date" not in rep1)
rep2=c.get(f"/flights/{F2}/report").data.decode()
chk("flight 2 has the season page", "Season to date" in rep2)

print("\n=== 3. zone numbers match the numbers on the image ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    ctx=appmod.report_context(fl)
    zones={}
    for g in ctx["agg"]["groups"]:
        for i,f in enumerate(g["findings"]): zones[g["zones"][i]]=round(f.area_acres,3)
    order=sorted(fl.findings,key=lambda f:(f.sort_order or 0,f.id))
chk("numbering follows the annotation order",
    all(zones[i+1]==round(f.area_acres,3) for i,f in enumerate(order)),
    {k:zones[k] for k in sorted(zones)[:6]})
chk("the two smallest areas are zones 5 and 6",
    zones.get(5)==0.023 and zones.get(6)==0.008,(zones.get(5),zones.get(6)))
print("\n  every zone, as the report numbers it:")
for z in sorted(zones): print(f"    zone {z:>2} = {zones[z]} ac")
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
