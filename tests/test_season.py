"""
Checks for the season-level trend: the arc across every flight of a season,
rather than only the comparison with the flight before.

Since V2 the season trend is an internal read only. The farmer's report is the
three pages the specification asks for and carries no season sheet, so the
checks below assert the maths still holds, that it still reaches the
agronomist's own flight screen, and that it stays out of the report.

Covers the fall back to the single-flight view, flights with no findings being
left out rather than plotted as zero, one season never bleeding into another,
and a twelve-flight season's findings paginating without losing a row.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_season.py
"""
import os, sys, tempfile, re, random
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"e.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")
import app as appmod, pdf_gen
appmod.app.config["TESTING"] = True
from models import db, Farm, Flight, Finding
from datetime import date
from flask import render_template
MEAN={"urgent":"Needs testing","watch":"Monitor","good":"Healthy"}
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d)))
    P,F=(P+1,F) if c else (P,F+1)

def mkfarm(name):
    fm=Farm(name=name,crop="Maize",acreage=40.0); db.session.add(fm); db.session.commit(); return fm
def mk(fm,n,mix,day=1,season="2026LR"):
    fl=Flight(farm_id=fm.id,season=season,flight_number=n,flights_planned=12,crop="Maize",
              acreage=40.0,flight_date=date(2026,3,1)+__import__("datetime").timedelta(days=day))
    db.session.add(fl); db.session.commit()
    k=0
    for sev,c in mix.items():
        for _ in range(c):
            db.session.add(Finding(flight_id=fl.id,category="Pest / Disease",colour_meaning=MEAN[sev],
                observation="o",likely_cause="c",recommendation="r",area_acres=1.5,sort_order=k)); k+=1
    db.session.commit(); return fl

c=appmod.app.test_client()
c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

print("\n=== A. single flight: no trend to draw ===")
with appmod.app.app_context():
    fm=mkfarm("Solo Farm"); one=mk(fm,1,{"urgent":2,"watch":1,"good":3},1); oid=one.id
    ctx=appmod.report_context(db.session.get(Flight,oid))
    chk("season is None with one flight", ctx["season"] is None, ctx["season"])
w=c.get(f"/flights/{oid}/report").data.decode()
chk("no season sheet in the farmer's report", "Your season so far" not in w)
chk("summary page rendered", "Field scouting summary" in w)
chk("map page rendered", "Farm map" in w)
chk("detailed findings rendered", "Detailed findings" in w)
_sheets=len(re.findall(r'<section class="sheet"', w))
chk("the report is three pages", _sheets==3, _sheets)
_claim=int(re.findall(r"Page \d+ of (\d+)", w)[0])
chk("footer page count matches the sheets drawn", _sheets==_claim, f"sheets={_sheets} claims={_claim}")

print("\n=== B. two flights: the smallest real season ===")
with appmod.app.app_context():
    fm=mkfarm("Pair Farm"); mk(fm,1,{"urgent":4,"good":2},1); two=mk(fm,2,{"urgent":1,"good":5},8); tid=two.id
    s=appmod.report_context(db.session.get(Flight,tid))["season"]
    chk("season summary exists", s is not None)
    chk("two points plotted", len(s["spark"]["points"])==2, len(s["spark"]["points"]))
    chk("direction computed", s["direction"] in ("up","down","flat"), s["direction"])
# The trend belongs to the people reading the field, not to the farmer's
# three-page report — so it shows on the flight screen and nowhere else.
w=c.get(f"/flights/{tid}/report").data.decode()
chk("trend stays out of the farmer's report", "Season to date" not in w)
d=c.get(f"/flights/{tid}").data.decode()
chk("trend shown on the agronomist's flight screen", "Season to date" in d)

print("\n=== C. a flight with no findings is skipped, not plotted as zero ===")
with appmod.app.app_context():
    fm=mkfarm("Gap Farm")
    mk(fm,1,{"urgent":3,"good":3},1)
    empty=Flight(farm_id=fm.id,season="2026LR",flight_number=2,flights_planned=12,
                 crop="Maize",acreage=40.0,flight_date=date(2026,3,8))
    db.session.add(empty); db.session.commit()
    cur=mk(fm,3,{"urgent":1,"good":5},15); cid=cur.id
    s=appmod.report_context(db.session.get(Flight,cid))["season"]
    nums=[p["number"] for p in s["points"]]
    chk("the empty flight is left out", nums==[1,3], nums)
    chk("no zero score invented", all(p["score"] is not None for p in s["scored"]),
        [p["score"] for p in s["scored"]])

print("\n=== D. seasons do not bleed into each other ===")
with appmod.app.app_context():
    fm=mkfarm("Two Season Farm")
    mk(fm,1,{"urgent":5},1,season="2025SR"); mk(fm,2,{"urgent":5},8,season="2025SR")
    mk(fm,1,{"good":5},20,season="2026LR"); cur=mk(fm,2,{"good":5},27,season="2026LR"); cid=cur.id
    ctx=appmod.report_context(db.session.get(Flight,cid))
    nums=[(p["flight"].season,p["number"]) for p in ctx["season"]["points"]]
    chk("only this season's flights appear", all(sn=="2026LR" for sn,_ in nums), nums)
    chk("exactly two of them", len(nums)==2, nums)

print("\n=== E. a long season paginates without losing a finding ===")
with appmod.app.app_context():
    fm=mkfarm("Long Season Farm")
    last=None
    for n in range(1,13):
        u=max(0,6-n//2); g=min(9,n)
        last=mk(fm,n,{"urgent":u,"watch":2,"good":g},n*7)
    lid=last.id
    s=appmod.report_context(db.session.get(Flight,lid))["season"]
    chk("all 12 plotted", len(s["spark"]["points"])==12, len(s["spark"]["points"]))
    with appmod.app.test_request_context("/"):
        fl=db.session.get(Flight,lid)
        html=render_template("report_print.html",pdf=True,public=True,share=None,
                             logo_uri="",map_uri="",**appmod.report_context(fl))
    data=pdf_gen.render_pdf(html, base_url=ROOT)
    open(os.path.join(TMP,"long.pdf"),"wb").write(data)
    sheets=len(re.findall(r'<section class="sheet"',html))
    import subprocess
    pages=int([l for l in subprocess.run(["pdfinfo",os.path.join(TMP,"long.pdf")],capture_output=True,text=True)
               .stdout.split("\n") if l.startswith("Pages")][0].split()[-1])
    chk("sheets == PDF pages (nothing overflowed)", sheets==pages, f"sheets={sheets} pages={pages}")
    # The real guarantee: every annotation reached the paper. Pagination that
    # silently drops a row would still produce a tidy page count.
    import subprocess as _sp
    txt=_sp.run(["pdftotext",os.path.join(TMP,"long.pdf"),"-"],capture_output=True,text=True).stdout
    zones=len(db.session.get(Flight,lid).findings)
    found=sum(1 for n in range(1,zones+1) if re.search(rf"(?m)^\s*{n}\s", txt))
    chk("every zone number printed", found==zones, f"found={found} of {zones}")

print("\n=== F. the internal score is computed consistently ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,lid); ctx=appmod.report_context(fl)
    chk("last season point == this flight's score",
        ctx["season"]["scored"][-1]["score"]==ctx["a"]["score"],
        (ctx["season"]["scored"][-1]["score"], ctx["a"]["score"]))

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
