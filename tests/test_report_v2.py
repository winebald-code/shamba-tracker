"""
Checks for the V2 report and the security headers.

The V2 report replaces V1's per-zone list with the few patterns actually present
in a flight's annotations. This drives the whole path against the real IPM Farm
data, because that flight is the worked example the V2 brief was written from —
if the clustering is right for it, the numbers on page 1 match a report a person
has already checked by hand.

It also asserts the phrasing the brief rules out. Tone is not decoration here:
"under pressure" and "work through these, in this order" are the specific lines
that prompted V2.

    python tests/test_report_v2.py
"""
import os,sys,tempfile,shutil,io,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"v2.db"),SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com",ADMIN_PASSWORD="password123")
import app as appmod, pdf_gen
from models import db,Farm,Flight,Finding
from datetime import date
from flask import render_template
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)

IPM=[(4.09,"Uneven germination, weeds","Overmounding / excess soil cover, water stress","Reduce soil cover, maintain proper mound height"),
 (3.76,"Reduced crop vigour","Soil fertility","Soil testing"),
 (3.01,"Poor plant vigour, red soil","Soil condition/fertility","Soil testing"),
 (2.54,"Reduced crop vigour, weeds overgrowth","Soil fertility","Soil testing and weeding"),
 (1.15,"Poor plant vigour","Nutrient deficiencies, soil condition","Soil testing"),
 (0.89,"Poor plant vigour","Soil condition","Soil testing"),
 (0.77,"Uneven growth","Water stress — low pressure at drip line end","Compare water vs. healthy section, adjust irrigation"),
 (0.76,"Poor plant vigour","Soil fertility","Soil testing"),
 (0.65,"Poor plant vigour","Inadequate moisture, nutrient uptake","Soil testing; compare water vs. healthy section"),
 (0.60,"Poor plant vigour","Soil condition, nutrient uptake","Soil testing"),
 (0.41,"Poor plant vigour","Soil condition","Soil testing"),
 (0.25,"Plant vigour","Water stress","Compare water collected vs. other section"),
 (0.24,"Plant vigour","Soil condition, loose covering","Soil testing"),
 (0.02,"Poor emergence","Water stress","Fix drip line on the ridge"),
 (0.01,"Weeds","Poor weed management","Weeding")]

with appmod.app.app_context():
    fm=Farm(name="IPM Farm",crop="Potatoes",acreage=38.0,farmer_name="J. Mwangi",location="Nakuru")
    db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=6,crop="Potatoes",
              acreage=38.0,flight_date=date(2026,8,12)); db.session.add(fl); db.session.commit()
    for i,(ac,o,c,r) in enumerate(IPM):
        db.session.add(Finding(flight_id=fl.id,category="Nutrient / Vigor",colour_meaning="Needs testing",
            colour_swatch="#D64550",observation=o,likely_cause=c,recommendation=r,
            area_text=f"{ac} ac",area_acres=ac,sort_order=i))
    db.session.commit()
    shutil.copy("samples/sample_annotated_map.jpg", os.path.join(appmod.UPLOAD_DIR,"ipm.jpg"))
    fl.map_image="ipm.jpg"; db.session.commit()
    FLID=fl.id; TOK=fl.share_token

c=appmod.app.test_client()
c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

print("=== security headers ===")
r=c.get("/")
for h,frag in [("Strict-Transport-Security",None),("Content-Security-Policy","frame-ancestors 'none'"),
               ("X-Frame-Options","DENY"),("X-Content-Type-Options","nosniff"),
               ("Referrer-Policy","strict-origin"),("Permissions-Policy","geolocation=()"),
               ("Cross-Origin-Opener-Policy","same-origin"),("Cross-Origin-Resource-Policy","same-origin")]:
    v=r.headers.get(h)
    if h=="Strict-Transport-Security":
        chk("HSTS withheld over plain HTTP", v is None, v)
    else:
        chk(f"{h} present", v and (frag in v), v)
r2=c.get("/",headers={"X-Forwarded-Proto":"https"})
chk("HSTS sent behind an HTTPS proxy","max-age=63072000" in (r2.headers.get("Strict-Transport-Security") or ""),
    r2.headers.get("Strict-Transport-Security"))

print("\n=== V2 report ===")
r=c.post(f"/flights/{FLID}/generate",follow_redirects=True)
chk("generates",r.status_code==200,r.status_code)
page=c.get(f"/flights/{FLID}/report").data.decode()
chk("three sheets on screen",len(re.findall(r'<section class="sheet"',page))==3,len(re.findall(r'<section class="sheet"',page)))
chk("summary heading present","Field scouting summary" in page)
chk("categories present","Issue categories" in page)
chk("observations present","Key observations" in page)
chk("suggestions present","Suggested areas to investigate" in page)
chk("detail page present","Detailed findings" in page)
chk("all 15 zones still listed",len(re.findall(r'data-l="Zone"',page))==15,len(re.findall(r'data-l="Zone"',page)))
print("\n=== tone: banned V1 phrasing is gone ===")
for bad in ["under pressure","small now, expensive later","Work through these","field health score",
            "in this order","Needs testing"]:
    chk(f"absent: {bad!r}", bad.lower() not in page.lower())

pdf=c.get(f"/flights/{FLID}/report.pdf")
chk("PDF returned",pdf.data[:5]==b"%PDF-",pdf.data[:12])
open(os.path.join(TMP,"v2.pdf"),"wb").write(pdf.data)
import subprocess
pages=int([l for l in subprocess.run(["pdfinfo",os.path.join(TMP,"v2.pdf")],capture_output=True,text=True).stdout.split("\n") if l.startswith("Pages")][0].split()[-1])
chk("exactly 3 PDF pages",pages==3,pages)
chk("public link works",appmod.app.test_client().get(f"/r/{TOK}").status_code==200)
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
