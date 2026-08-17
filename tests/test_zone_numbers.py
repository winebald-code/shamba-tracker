"""
Checks that the review page and the report call a zone by the same number.

The report numbers a zone by how much ground it covers, worst first, and the
review page used to number its rows in the order the DroneDeploy export happened
to list them. Both were called "1", "2", "3" and they meant different areas: on
the reference flight, the 3.76-acre zone was row 1 on the review page and zone 2
on the farmer's report. An agronomist correcting "zone 2" was editing zone 3.

So this asserts the two agree row for row, field for field, and that a summary
sentence covering several zones says which ones it means.

    python tests/test_zone_numbers.py
"""
import os,sys,tempfile,shutil,io,re,html
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"z.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, aggregation
from models import db,Farm,Flight
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
              acreage=38.0,flight_date=date(2026,8,12)); db.session.add(fl); db.session.commit(); FLID=fl.id
c.post(f"/flights/{FLID}/upload_csv",data={"csv":(io.BytesIO(CSV),"dd.csv")},
       content_type="multipart/form-data",follow_redirects=True)
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID)
    for f in fl.findings:
        if not (f.likely_cause or "").strip(): f.likely_cause="Water stress"
    shutil.copy(os.path.join(ROOT,"samples","sample_annotated_map.jpg"),os.path.join(appmod.UPLOAD_DIR,"zm.jpg"))
    fl.map_image="zm.jpg"; db.session.commit()

review=c.get(f"/flights/{FLID}").data.decode()
report=c.get(f"/flights/{FLID}/report").data.decode()

def flat(x): return html.unescape(re.sub(r"\s+"," ",x)).strip()

# ---- what each page says, keyed by the number it prints against the row ----
seen={}
for block in re.split(r'(?=<div id="f\d+" data-fid=)',review)[1:]:
    n=re.search(r'font-bold text-\[13px\] tnum">(\d+)</span>',block)
    fields=dict(re.findall(r'data-field="(observation|likely_cause|recommendation)"[^>]*>(.*?)</textarea>',block,re.S))
    if n and len(fields)==3:
        seen[int(n.group(1))]=tuple(flat(fields[k]) for k in ("observation","likely_cause","recommendation"))

printed={}
for row in re.findall(r'<tr>\s*<td class="n" data-l="Zone">(.*?)</tr>',report,re.S):
    n=int(re.search(r'nchip"[^>]*>(\d+)</span>',row).group(1))
    cells=re.findall(r'data-l="(?:Observation|Likely cause|Recommendation)">(.*?)</td>',row,re.S)
    printed[n]=tuple(flat(x) for x in cells)

print("=== 1. the two pages agree ===")
chk("every finding is on the review page",len(seen)==15,len(seen))
chk("every finding is on the report",len(printed)==15,len(printed))
chk("the same zone numbers on both",sorted(seen)==sorted(printed),(sorted(seen),sorted(printed)))
chk("zone numbers run 1..n with no gaps",sorted(printed)==list(range(1,len(printed)+1)),sorted(printed))
mismatched=[n for n in sorted(set(seen)|set(printed)) if seen.get(n)!=printed.get(n)]
chk("each zone carries identical text on both",not mismatched,mismatched[:3])

print("\n=== 2. the review page reads in the report's order ===")
order=[int(n) for n in re.findall(r'font-bold text-\[13px\] tnum">(\d+)</span>',review)]
chk("the review page counts up",order==sorted(order),order)
chk("the column is labelled Zone","<div>Zone</div>" in review)
chk("and no longer Pin","<div>Pin</div>" not in review)

print("\n=== 3. the largest zone is zone 1 ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,FLID); ctx=appmod.report_context(fl)
    nums=ctx["a"]["numbers"]
    biggest=max(fl.findings,key=lambda f:f.area_acres or 0)
chk("the biggest area is numbered 1",nums[biggest.id]==1,nums[biggest.id])
chk("4.09 acres is the biggest",round(biggest.area_acres,2)==4.09,biggest.area_acres)

print("\n=== 4. a summary sentence names the zones it covers ===")
with appmod.app.app_context():
    groups=appmod.report_context(db.session.get(Flight,FLID))["agg"]["groups"]
for g in groups:
    s=str(g["suggestion"])
    if g["count"]==1:
        chk(f"{g['name']}: the one zone is named",f"zone {g['zones'][0]}" in s,s[:70])
    else:
        listed=all(re.search(rf"\b{z}\b",s.split(", the agronomist")[0]) for z in g["zones"])
        chk(f"{g['name']}: all {g['count']} zones are named",listed,s[:110])
        chk(f"{g['name']}: reads 'zones' not a bare count","in this pattern (zones " in s,s[:110])
multi=[g for g in groups if g["count"]>1]
chk("the flight really does have a multi-zone pattern",bool(multi),len(multi))
chk("the zone list is on the report",
    all(str(g["suggestion"]).split(", the agronomist")[0].replace("<b>","") in flat(report)
        for g in multi))

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
