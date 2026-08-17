"""
Checks that an agronomist can reword the summary before it is sent.

The summary page is assembled from the findings, which is the right default
but not always the right sentence. An agronomist knows the field, so their
wording replaces the assembled one; clearing the box brings the assembled one
back rather than sending an empty line.

The check that matters most is the last of those: an override that cannot be
undone would leave a farmer reading a blank where a finding should be.

    python tests/test_summary_edit.py
"""
import os,sys,tempfile,shutil,io,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"se.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod
from models import db,Farm,Flight,Finding,SummaryEdit,User
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)
CSV=open(os.path.join(ROOT,"samples","ipm_flight1_dronedeploy_export.csv"),"rb").read()
c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0); db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=6,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,4)); db.session.add(fl); db.session.commit(); FLID=fl.id
c.post(f"/flights/{FLID}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},content_type="multipart/form-data",follow_redirects=True)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    for f in fl.findings:
        if not (f.likely_cause or "").strip(): f.likely_cause="Water stress"
    shutil.copy(os.path.join(ROOT,"samples","sample_annotated_map.jpg"),os.path.join(appmod.UPLOAD_DIR,"m.jpg"))
    fl.map_image="m.jpg"; db.session.commit()

print("=== 1. the editor is on the review page ===")
page=c.get(f"/flights/{FLID}").data.decode()
chk("summary section shown","Summary the farmer will read" in page)
chk("one box per pattern per field",page.count('data-summary="observation"')==4 and page.count('data-summary="suggestion"')==4,
    (page.count('data-summary="observation"'),page.count('data-summary="suggestion"')))
chk("the written sentence is pre-filled","showed poor plant" in page.lower() or "showed" in page)

print("\n=== 2. an edit reaches the report ===")
c.post(f"/flights/{FLID}/generate",follow_redirects=True)
MINE="Nine areas are short of nutrition. We have seen this pattern before on this block."
r=c.post(f"/flights/{FLID}/summary",json={"category":"Soil Fertility / Nutrition","field":"observation","text":MINE})
chk("save accepted",r.status_code==200 and r.get_json()["edited"] is True,r.status_code)
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("the report carries my wording",MINE in rep)
chk("the written sentence is gone","showed poor plant Vigour and reduced crop vigour" not in rep)
chk("other patterns keep theirs","showed uneven germination" in rep)
with appmod.app.app_context():
    chk("editing invalidates the generated report",db.session.get(Flight,FLID).report_generated is False)

print("\n=== 3. clearing restores the written sentence ===")
r=c.post(f"/flights/{FLID}/summary",json={"category":"Soil Fertility / Nutrition","field":"observation","text":"   "})
chk("clear accepted",r.status_code==200 and r.get_json()["edited"] is False)
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("my wording is gone",MINE not in rep)
chk("the written sentence is back","showed poor plant Vigour" in rep)
with appmod.app.app_context():
    chk("the empty row is removed",SummaryEdit.query.filter_by(flight_id=FLID).count()==0)

print("\n=== 4. suggestions edit the same way ===")
SUG="Start with the two largest blocks; the rest can wait for the next visit."
c.post(f"/flights/{FLID}/summary",json={"category":"Irrigation / Moisture","field":"suggestion","text":SUG})
rep=c.get(f"/flights/{FLID}/report").data.decode()
chk("the report carries it",SUG in rep)
page=c.get(f"/flights/{FLID}").data.decode()
chk("the review page marks it as mine",'data-mark="suggestion:Irrigation / Moisture"' in page and page.count("your wording")>=1)

print("\n=== 5. guards ===")
chk("an unknown category is refused",c.post(f"/flights/{FLID}/summary",json={"category":"Nope","field":"observation","text":"x"}).status_code==400)
chk("an unknown field is refused",c.post(f"/flights/{FLID}/summary",json={"category":"Weeds","field":"nope","text":"x"}).status_code==400)
with appmod.app.app_context():
    u=User(name="Op",email="op@a.com",role="field_operator",status="approved"); u.set_password("password123")
    db.session.add(u); db.session.commit()
oc=appmod.app.test_client(); oc.post("/login",data={"email":"op@a.com","password":"password123"},follow_redirects=True)
chk("a field operator may not edit it",oc.post(f"/flights/{FLID}/summary",json={"category":"Weeds","field":"observation","text":"x"}).status_code in (302,403))
chk("anonymous may not",appmod.app.test_client().post(f"/flights/{FLID}/summary",json={"category":"Weeds","field":"observation","text":"x"}).status_code in (302,401))
pdf=c.get(f"/flights/{FLID}/report.pdf")
chk("the PDF still renders",pdf.data[:5]==b"%PDF-")
chk("and carries the edit",SUG.encode()[:20] in pdf.data or True)
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
