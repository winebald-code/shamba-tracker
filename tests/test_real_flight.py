"""
Checks the real IPM Farm flight, end to end.

The brief's worked example was written from this flight, so its DroneDeploy
export is the one dataset where a correct answer is already known: nine soil
fertility areas, one crop establishment, four irrigation and one weeds.

It exists because every pin on this flight is the same colour. The category
used to be suggested from that colour, so all fifteen arrived as "Pest /
Disease" whatever the agronomist had written, and the review page and the
report disagreed with each other and with the notes.

    python tests/test_real_flight.py
"""
import os,sys,tempfile,shutil,io,re,subprocess
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"r.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, parsing, aggregation, schema
from models import db,Farm,Flight,Finding
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)
CSV=open(os.path.join(ROOT,"samples","ipm_flight1_dronedeploy_export.csv"),"rb").read()

with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0); db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=12,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,4),
              dronedeploy_project_url="https://www.dronedeploy.com/app2/sites/x")
    db.session.add(fl); db.session.commit(); FLID=fl.id
c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
r=c.post(f"/flights/{FLID}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},
         content_type="multipart/form-data",follow_redirects=True)
chk("CSV imports",r.status_code==200,r.status_code)

print("\n=== 1. the review page no longer files everything as Pest / Disease ===")
from collections import Counter
with appmod.app.app_context():
    cats=Counter(f.category for f in db.session.get(Flight,FLID).findings)
chk("15 findings imported",sum(cats.values())==15,sum(cats.values()))
for name,n in [("Soil Fertility / Nutrition",9),("Irrigation / Moisture",4),
               ("Crop Establishment",1),("Weeds",1)]:
    chk(f"{name}: {n}",cats.get(name)==n,cats.get(name))
chk("nothing left under Pest / Disease",cats.get("Pest / Disease") is None,cats.get("Pest / Disease"))

print("\n=== 2. the report groups exactly as Cathy's worked example ===")
with appmod.app.app_context():
    # One pin left its likely cause blank in the export, so a report cannot be
    # generated until somebody fills it in. This is the value the agronomist
    # entered on the review page for that pin.
    for f in db.session.get(Flight,FLID).findings:
        if not (f.likely_cause or "").strip():
            f.likely_cause = "Water stress"
    db.session.commit()
    shutil.copy(os.path.join(ROOT,"samples","sample_annotated_map.jpg"),os.path.join(appmod.UPLOAD_DIR,"m.jpg"))
    db.session.get(Flight,FLID).map_image="m.jpg"; db.session.commit()
    ctx=appmod.report_context(db.session.get(Flight,FLID))
    got={g["name"]:(g["count"],g["acres_text"]) for g in ctx["agg"]["groups"]}
for name,cnt,ac in [("Soil Fertility / Nutrition",9,"13.4"),("Crop Establishment",1,"4.1"),
                    ("Irrigation / Moisture",4,"1.7"),("Weeds",1,"0.01")]:
    chk(f"{name}: {cnt} areas ~{ac} ac",got.get(name)==(cnt,ac),got.get(name))
chk("exactly four patterns",len(got)==4,list(got))
chk("total reads 19.2 acres",ctx["agg"]["total_acres_text"]=="19.2",ctx["agg"]["total_acres_text"])

print("\n=== 3. the summary and the map legend agree ===")
page=c.get(f"/flights/{FLID}/report").data.decode()
for name in got: chk(f"'{name}' appears on the report",name in page)
chk("three sheets",len(re.findall(r'<section class="sheet"',page))==3)
chk("trailing colons dropped from the summary","vigour:" not in page.split("Detailed findings")[0].lower())

print("\n=== 4. the migration re-reads a colour-guessed category ===")
with appmod.app.app_context():
    f=db.session.get(Flight,FLID).findings[0]
    fid, real = f.id, f.category
    f.category="Pest / Disease"; db.session.commit()      # as the old code stored it
    schema.reclassify_colour_guesses(db); db.session.expire_all()
    chk("re-read from the agronomist's text",db.session.get(Finding,fid).category==real,
        db.session.get(Finding,fid).category)
    # a category a person chose is left alone
    f=db.session.get(Finding,fid); f.category="Needs Investigation"; db.session.commit()
    schema.reclassify_colour_guesses(db); db.session.expire_all()
    chk("a hand-set category is left alone",
        db.session.get(Finding,fid).category=="Needs Investigation",
        db.session.get(Finding,fid).category)

pdf=c.get(f"/flights/{FLID}/report.pdf")
open(os.path.join(TMP,"real.pdf"),"wb").write(pdf.data)
n=int([l for l in subprocess.run(["pdfinfo",os.path.join(TMP,"real.pdf")],capture_output=True,text=True).stdout.split("\n") if l.startswith("Pages")][0].split()[-1])
chk("PDF is 3 pages",n==3,n)
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
