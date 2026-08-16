"""
Checks that every page works on a phone.

The report is composed as fixed 210 x 297 mm sheets, which is right for paper
and wrong for a 380px screen, so it has to reflow below a breakpoint. The rest
of the application is checked for in-flow boxes wider than a phone viewport,
which is what causes the sideways scroll that makes a page feel broken.

    python tests/test_responsive.py
"""
import os,sys,tempfile,shutil,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"m.db"),SECRET_KEY="t",ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod
from models import db,Farm,Flight,Finding
from datetime import date
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)
with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0); db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=6,crop="Potatoes",
          acreage=38.0,flight_date=date(2026,8,12),
          dronedeploy_project_url="https://www.dronedeploy.com/app2/sites/example")
    db.session.add(fl); db.session.commit()
    for i,(ac,o,c_,r) in enumerate([(4.09,"Uneven germination, weeds","Overmounding / excess soil cover","Reduce soil cover"),
                                    (3.76,"Reduced crop vigour","Soil fertility","Soil testing"),
                                    (0.77,"Uneven growth","Water stress at drip line end","Adjust irrigation")]):
        db.session.add(Finding(flight_id=fl.id,category="Nutrient / Vigor",colour_meaning="Needs testing",
            observation=o,likely_cause=c_,recommendation=r,area_acres=ac,sort_order=i))
    db.session.commit()
    shutil.copy("samples/sample_annotated_map.jpg",os.path.join(appmod.UPLOAD_DIR,"m.jpg"))
    fl.map_image="m.jpg"; db.session.commit(); FLID=fl.id; TOK=fl.share_token
c=appmod.app.test_client(); c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

print("=== responsive rules ===")
page=c.get(f"/flights/{FLID}/report").data.decode()
chk("report has a mobile breakpoint","@media screen and (max-width:820px)" in page)
chk("sheet width resets on small screens", re.search(r'max-width:820px\)\s*\{.*?\.sheet\s*\{[^}]*width:100%', page, re.S) is not None)
chk("detail tables reflow to cards", "data-l=" in page and ".dt thead { display:none; }" in page)
chk("viewport meta present", 'name="viewport"' in page)
print("\n=== the report on a large screen ===")
# The sheets are a fixed 210mm. In a wide container they would sit against the
# left edge with the rest of the width empty, which is what a laptop shows.
chk("sheets are centred in their container", "margin-left:auto; margin-right:auto;" in page)
chk("sheets are separated on screen only", "@media screen {" in page and "box-shadow" in page)
chk("the print path takes no screen padding", "@media print  { .rdwrap { padding: 0; }" in page)

print("\n=== the interactive map link is visible ===")
# The document sets a { color:inherit; text-decoration:none } so it reads as a
# printed page, which left this link looking exactly like body text.
chk("rendered as a card rather than plain text", 'class="ddlink"' in page)
chk("the card has its styles", ".ddlink { display:block;" in page)

print("\n=== every page carries a viewport and no fixed-width overflow ===")
for path,label in [("/","homepage"),("/login","login"),(f"/flights/{FLID}","review"),
                   ("/farms","farms"),("/homepage","homepage editor"),("/import","import"),
                   (f"/r/{TOK}","public report")]:
    cc = appmod.app.test_client() if path.startswith("/r/") else c
    if path=="/": cc=appmod.app.test_client()
    r=cc.get(path if path!="/" else "/?preview=1", follow_redirects=True)
    body=r.data.decode()
    ok = r.status_code==200 and 'name="viewport"' in body
    # A decorative absolutely-positioned shape is clipped by its overflow-hidden
    # parent and cannot widen the page, so only in-flow boxes are checked.
    bad = [m for m in re.findall(r'class="([^"]*(?:(?<![-\w:])(?:w|min-w)-\[\d{3,}px\])[^"]*)"', body)
           if "absolute" not in m]
    chk(f"{label}: renders with a viewport, no fixed wide box", ok and not bad, (r.status_code, bad[:2]))
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
