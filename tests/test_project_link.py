"""
Checks that a report's "Open the interactive map" link follows the farm.

A flight used to be handed a *copy* of the farm's DroneDeploy project URL when
it was created, and the report rendered that copy. A copy stops tracking what it
came from, so correcting the link on the farm changed nothing in any report and
the only way to fix it was to delete the farm and enter it again.

The link now lives on the farm, a flight follows it, and a flight may still
carry one of its own where it was flown against a different map. This asserts
all three, including that an edit reaches a report that was already generated.

    python tests/test_project_link.py
"""
import os,sys,tempfile,shutil,io
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"pl.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, schema
from models import db,Farm,Flight
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)

OLD="https://www.dronedeploy.com/app2/sites/OLDSITE/maps/OLDMAP"
NEW="https://www.dronedeploy.com/app2/sites/NEWSITE/maps/NEWMAP"
OWN="https://www.dronedeploy.com/app2/sites/NEWSITE/maps/FLIGHTOWN"
CSV=open(os.path.join(ROOT,"samples","ipm_flight1_dronedeploy_export.csv"),"rb").read()

c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
with appmod.app.app_context():
    fm=Farm(name="Link Farm",crop="Potatoes",acreage=38.0,dronedeploy_project_url=OLD)
    db.session.add(fm); db.session.commit(); FARM=fm.id
c.post(f"/farms/{FARM}/flights/new",data={"season":"2026LR","flight_number":"1","flights_planned":"6",
       "crop":"Potatoes","acreage":"38","flight_date":"2026-08-12"},follow_redirects=True)
with appmod.app.app_context():
    FLID=Flight.query.filter_by(farm_id=FARM).first().id
c.post(f"/flights/{FLID}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},
       content_type="multipart/form-data",follow_redirects=True)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    for f in fl.findings:
        if not (f.likely_cause or "").strip(): f.likely_cause="Water stress"
    shutil.copy(os.path.join(ROOT,"samples","sample_annotated_map.jpg"),os.path.join(appmod.UPLOAD_DIR,"lm.jpg"))
    fl.map_image="lm.jpg"; db.session.commit()
c.post(f"/flights/{FLID}/generate",follow_redirects=True)

print("=== 1. a new flight follows the farm rather than copying it ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    chk("nothing was copied onto the flight",(fl.dronedeploy_project_url or "")=="" ,repr(fl.dronedeploy_project_url))
    chk("but the flight resolves the farm's link",fl.project_url==OLD,fl.project_url)
chk("the report carries it",OLD in c.get(f"/flights/{FLID}/report").data.decode())

print("\n=== 2. editing the farm reaches the report ===")
c.post(f"/farms/{FARM}/edit",data={"name":"Link Farm","crop":"Potatoes","acreage":"38","location":"",
       "farmer_name":"","farmer_email":"","farmer_phone":"","dronedeploy_project_url":NEW,"notes":""},
       follow_redirects=True)
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("the report shows the new link",NEW in rep)
chk("and not the old one",OLD not in rep)
chk("the review page shows the new link",NEW in c.get(f"/flights/{FLID}").data.decode())
with appmod.app.app_context():
    chk("the farm still has exactly one flight",len(db.session.get(Farm,FARM).flights)==1)
pdf=c.get(f"/flights/{FLID}/report.pdf")
chk("the PDF still renders",pdf.data[:5]==b"%PDF-")

print("\n=== 3. a flight may still point somewhere of its own ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID); fl.dronedeploy_project_url=OWN; db.session.commit()
    chk("its own link wins",fl.project_url==OWN,fl.project_url)
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("the report follows the flight",OWN in rep)
chk("the farm's link steps aside",f'href="{NEW}"' not in rep)

print("\n=== 4. the start-up pass releases a copy, and only a copy ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID); fl.dronedeploy_project_url=NEW; db.session.commit()
    schema.release_copied_project_urls(db)
    db.session.expire_all()
    fl=db.session.get(Flight,FLID)
    chk("a flight holding the farm's own link is released",(fl.dronedeploy_project_url or "")=="")
    chk("and still resolves to the same page",fl.project_url==NEW,fl.project_url)
    fl.dronedeploy_project_url=OWN; db.session.commit()
    schema.release_copied_project_urls(db); db.session.expire_all()
    chk("a flight pointing elsewhere is left alone",
        db.session.get(Flight,FLID).dronedeploy_project_url==OWN)
    schema.release_copied_project_urls(db); db.session.expire_all()
    chk("running it twice changes nothing",
        db.session.get(Flight,FLID).dronedeploy_project_url==OWN)

print("\n=== 5. a farm with no link at all ===")
with appmod.app.app_context():
    bare=Farm(name="No Link Farm",crop="Maize"); db.session.add(bare); db.session.commit()
    fl2=Flight(farm_id=bare.id,season="2026LR",flight_number=1,crop="Maize",
               flight_date=date(2026,8,12)); db.session.add(fl2); db.session.commit()
    chk("resolves to nothing rather than raising",fl2.project_url=="",repr(fl2.project_url))
    fl2.dronedeploy_project_url=OWN; db.session.commit()
    chk("the flight's own link is used when the farm has none",fl2.project_url==OWN)

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
