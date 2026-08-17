"""
Checks for the season-level trend: the arc across every flight of a season,
rather than only the comparison with the flight before.

Covers the fall back to the single-flight view, flights with no findings being
left out rather than plotted as zero, one season never bleeding into another,
and a full twelve-flight season still fitting on one A4 sheet.

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

print("\n=== A. single flight: no season sheet, falls back to baseline ===")
with appmod.app.app_context():
    fm=mkfarm("Solo Farm"); one=mk(fm,1,{"urgent":2,"watch":1,"good":3},1); oid=one.id
    ctx=appmod.report_context(db.session.get(Flight,oid))
    chk("season is None with one flight", ctx["season"] is None, ctx["season"])
w=c.get(f"/flights/{oid}/report").data.decode()
# V2 has no season sheet: the trend appears as a line on page 1, and only once
# more than one flight of the season carries findings.
# One flight is a reading, not a trend, so the season sheet is absent until a
# second flight of the season carries findings.
chk("no season sheet with a single flight", "Season to date" not in w)
_sheets=len(re.findall(r'<section class="sheet"', w))
_claim=int(re.findall(r"Page \d+ of (\d+)", w)[0])
chk("footer page count matches the sheets drawn", _sheets==_claim, f"sheets={_sheets} claims={_claim}")

print("\n=== B. two flights: the smallest real season ===")
with appmod.app.app_context():
    fm=mkfarm("Pair Farm"); mk(fm,1,{"urgent":4,"good":2},1); two=mk(fm,2,{"urgent":1,"good":5},8); tid=two.id
    s=appmod.report_context(db.session.get(Flight,tid))["season"]
    chk("season summary exists", s is not None)
    chk("two points plotted", len(s["spark"]["points"])==2, len(s["spark"]["points"]))
    chk("direction computed", s["direction"] in ("up","down","flat"), s["direction"])
w=c.get(f"/flights/{tid}/report").data.decode()
chk("the season sheet appears once there are two flights", "Season to date" in w)

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

print("\n=== E. a full 12-flight season still fits one A4 sheet ===")
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

print("\n=== F. score shown on the season page equals the cover score ===")
with appmod.app.app_context():
    fl=db.session.get(Flight,lid); ctx=appmod.report_context(fl)
    chk("last season point == cover score",
        ctx["season"]["scored"][-1]["score"]==ctx["a"]["score"],
        (ctx["season"]["scored"][-1]["score"], ctx["a"]["score"]))

print("\n=== G. the season sheet reports counts, not a score ===")
# A number between 0 and 100 states a verdict the annotations do not support, so
# the sheet plots areas flagged and the ground they cover instead.
import aggregation as _agg
with appmod.app.app_context():
    _fl = db.session.get(Flight, lid)
    _season = Flight.query.filter_by(farm_id=_fl.farm_id, season=_fl.season).all()
    _so = _agg.season_overview(_season, _fl.id)
chk("an overview is produced for a season of many flights", _so is not None)
chk("every reported flight is a point", _so and _so["flights"] == len(_so["points"]),
    _so["flights"] if _so else None)
chk("the current flight is marked once",
    sum(1 for p in _so["points"] if p["is_current"]) == 1)
chk("each point carries counts and acreage",
    all("count" in p and "acres" in p for p in _so["points"]))
chk("bars are split by report category",
    all(all(s["colour"] in _agg.CATEGORY_COLOURS.values() for s in p["segments"])
        for p in _so["points"]))
chk("only categories the season produced appear in the key",
    all(name in _agg.CATEGORY_ORDER for name, _c in _so["categories"]), _so["categories"])
with appmod.app.app_context():
    _one = _agg.season_overview([db.session.get(Flight, lid)], lid)
chk("a single flight yields no overview", _one is None, _one)

# ---------------------------------------------------------------- the cut-off
# "Season to date" is meant literally. A report is the record of what was known
# on the day it went out, so a flight flown later must not appear on an earlier
# flight's page — generating flight 2 used to put a season sheet onto flight 1
# as well, showing the field two months after the report describing it.
print("\n=== the season stops at the flight being reported on ===")
with appmod.app.app_context():
    _all = Flight.query.filter_by(farm_id=db.session.get(Flight, lid).farm_id).all()
    _seasoned = [f for f in _all if f.season == db.session.get(Flight, lid).season]
    _first = min(_seasoned, key=lambda f: (f.flight_number or 0, f.id or 0))
    _early = _agg.season_overview(_seasoned, _first.id)
    _late = _agg.season_overview(_seasoned, lid)
    _first_id, _first_no = _first.id, _first.flight_number
chk("the first flight of a season has nothing to compare against", _early is None, _early)
chk("a later flight does", _late is not None)
chk("no point is numbered after the flight being reported on",
    all((p["number"] or 0) <= (_late["last"]["number"] or 0) for p in _late["points"]))

print("\n=== the first flight's report carries no season sheet ===")
_p1 = c.get(f"/flights/{_first_id}/report").data.decode()
chk("no season sheet on flight 1", "Season to date" not in _p1)
_pl = c.get(f"/flights/{lid}/report").data.decode()
chk("a season sheet once there is a comparison", "Season to date" in _pl)

print("\n=== every number on the chart is stated ===")
chk("the chart states its scale", _late["peak"] == max(p["count"] for p in _late["points"]),
    _late["peak"])
chk("three ticks label the scale", len(_late["ticks"]) == 3, _late["ticks"])
chk("the ticks run from the peak down to zero",
    [t["n"] for t in _late["ticks"]] == [_late["peak"], round(_late["peak"] / 2.0), 0],
    _late["ticks"])
chk("every segment knows how tall it is",
    all("px" in s and "label_fits" in s for p in _late["points"] for s in p["segments"]))
chk("a label is only placed where it fits",
    all(s["label_fits"] == (s["px"] >= _agg.CHART_LABEL_MIN_PX)
        for p in _late["points"] for s in p["segments"]))
chk("the scale caption is on the page", f"scale 0 to {_late['peak']}" in _pl)
chk("each bar states its own total",
    all(f'<div class="tot mono">{p["count"]}</div>' in _pl for p in _late["points"]))
chk("every category count appears under its bar",
    all(f'title="{s["name"]}">{s["n"]}</span>' in _pl
        for p in _late["points"] for s in p["segments"]))

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
