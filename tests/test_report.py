"""
Checks for the report: the farmer's link, the download name, and the promise
that what is on screen is what comes out of the printer.

Runs against a throwaway SQLite file with a real Flask test client, so the
templates actually render. Run it with:

    python tests/test_report.py
"""
import os, sys, tempfile, sqlite3, re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
TMP=tempfile.mkdtemp(); DB=os.path.join(TMP,"test_report.db")
os.environ.update(DATABASE_URL="sqlite:///"+DB, SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")
os.environ.pop("PUBLIC_BASE_URL", None)
import app as appmod
from models import db, Farm, Flight, Finding
from datetime import date
import integrations
APP=appmod.app; APP.config["TESTING"]=True
P=F=0
def chk(label, cond, detail=""):
    global P,F
    print(("  [PASS] " if cond else "  [FAIL] ")+label+("" if cond else "  -> "+str(detail)))
    P,F = (P+1,F) if cond else (P,F+1)

with APP.app_context():
    fm=Farm(name="Kilimo Bora Farm",crop="Maize",acreage=42.0,farmer_phone="0712345678")
    db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id,season="2026LR",flight_number=1,flights_planned=3,crop="Maize",
              acreage=42.0,flight_date=date(2026,3,14))
    db.session.add(fl); db.session.commit()
    db.session.add(Finding(flight_id=fl.id,category="Pest / Disease",colour_meaning="Needs testing",
        colour_swatch="#f50057",observation="o",likely_cause="c",recommendation="r",
        area_text="3.8 ac",area_acres=3.8))
    db.session.commit(); FID=fl.id

print("\n=== 1. the original bug: a flight with no share token ===")
sqlite3.connect(DB).executescript("UPDATE flights SET share_token=NULL;")
with APP.app_context():
    fl=db.session.get(Flight,FID)
    chk("token is genuinely NULL before the fix runs", fl.share_token is None, fl.share_token)

with APP.app_context():
    import schema
    schema.backfill_share_tokens(db)
    db.session.expire_all()
    fl=db.session.get(Flight,FID)
    chk("boot-time backfill issues a token", bool(fl.share_token), fl.share_token)
    chk("public URL no longer points at /r/None",
        "/r/None" not in integrations.flight_public_url(fl,"https://x.dev"),
        integrations.flight_public_url(fl,"https://x.dev"))
    TOK=fl.share_token

c=APP.test_client()
r=c.get(f"/r/{TOK}")
chk("the farmer's report link opens", r.status_code==200, r.status_code)

# and the lazy mint, for a row that slips through
sqlite3.connect(DB).executescript("UPDATE flights SET share_token='';")
c2=APP.test_client(); c2.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)
r=c2.get(f"/flights/{FID}/report")
with APP.app_context():
    db.session.expire_all()
    chk("viewing a report mints a missing token on the spot",
        bool(db.session.get(Flight,FID).share_token))
    TOK=db.session.get(Flight,FID).share_token   # the mint issued a new one

print("\n=== 2. links behind a TLS-terminating proxy ===")
r=c.get(f"/r/{TOK}", headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":"shamba.example.com"})
chk("proxied request is served", r.status_code==200, r.status_code)
with APP.test_request_context("/", headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":"shamba.example.com"},
                              environ_overrides={"wsgi.url_scheme":"https","HTTP_HOST":"shamba.example.com"}):
    base=appmod._public_base()
    chk("public base is https on a real host", base.startswith("https://"), base)
with APP.test_request_context("/", environ_overrides={"HTTP_HOST":"localhost:5000"}):
    chk("localhost is left on http", appmod._public_base().startswith("http://localhost"),
        appmod._public_base())

print("\n=== 3. download filename ===")
with APP.app_context():
    fl=db.session.get(Flight,FID)
    chk("Farm Name_Crop Name_Season Year_Flight No",
        fl.report_filename=="Kilimo Bora Farm_Maize_2026LR_Flight 1.pdf", fl.report_filename)
    fl.farm.name="O'Brien / Kariuki  Farm"; fl.crop="Maize (H614)"
    chk("illegal characters stripped, spaces kept",
        fl.report_filename=="O'Brien Kariuki Farm_Maize (H614)_2026LR_Flight 1.pdf", fl.report_filename)
    fl.farm.name="Kilimo Bora Farm"; fl.crop="Maize"
    # two flights of one season must not collide in the farmer's downloads
    fl.flight_number=7
    chk("the flight number moves with the flight",
        fl.report_filename=="Kilimo Bora Farm_Maize_2026LR_Flight 7.pdf", fl.report_filename)
    chk("the on-disk slug carries it too", fl.slug=="Kilimo_Bora_Farm_Maize_2026LR_F7", fl.slug)
    fl.flight_number=1; db.session.commit()

r=c2.get(f"/flights/{FID}/report.pdf")
cd=r.headers.get("Content-Disposition","")
chk("header carries the readable name", 'filename="Kilimo Bora Farm_Maize_2026LR_Flight 1.pdf"' in cd, cd)
chk("header carries an RFC 5987 copy", "filename*=UTF-8''" in cd, cd)
chk("a real PDF comes back", r.data[:5]==b"%PDF-", r.data[:12])

print("\n=== 4. the download lands as a file, automatically ===")
import pdf_gen, time
chk("this host can generate PDFs", pdf_gen.PDF_AVAILABLE)
r=c2.get(f"/flights/{FID}/report.pdf")
chk("served as application/pdf", r.headers.get("Content-Type")=="application/pdf",
    r.headers.get("Content-Type"))
chk("sent as an attachment, not opened in a viewer",
    r.headers.get("Content-Disposition","").startswith("attachment;"),
    r.headers.get("Content-Disposition"))
chk("no redirect to the print dialog", r.status_code==200, r.status_code)
chk("Content-Length is set so the browser can show progress",
    r.headers.get("Content-Length")==str(len(r.data)), r.headers.get("Content-Length"))
t=time.time(); c2.get(f"/flights/{FID}/report.pdf"); warm=time.time()-t
chk("a repeat download is served from cache", warm < 0.15, f"{warm:.2f}s")
page=c2.get(f"/flights/{FID}/report").data.decode()
chk("the link carries download=\"Farm Name_Crop Name_Season Year_Flight No.pdf\"",
    'download="Kilimo Bora Farm_Maize_2026LR_Flight 1.pdf"' in page)
with APP.app_context():
    fl=db.session.get(Flight,FID); fl.agronomist_note="edited"; db.session.commit()
r2=c2.get(f"/flights/{FID}/report.pdf")
chk("editing the report invalidates the cached file", r2.data != r.data)

print("\n=== 5. one typeface, bundled ===")
doc=c.get(f"/r/{TOK}").data.decode()
chk("no IBM Plex Mono anywhere in the document", "IBM Plex" not in doc)
chk("no monospace fallback left behind", "monospace" not in doc)
chk("Montserrat is the only bundled face",
    "montserrat-latin-800-normal.woff2" in doc and "ibm-plex" not in doc.lower())
chk("readings use tabular figures instead of a second family",
    "font-variant-numeric:tabular-nums" in doc and "font-feature-settings:'tnum' 1" in doc)
import glob as _g
fonts=sorted(os.path.basename(f) for f in _g.glob(os.path.join(ROOT_DIR,"static","fonts","*.woff2")))
chk("only Montserrat files ship", all(f.startswith("montserrat-") for f in fonts), fonts)

print("\n=== 5b. nothing is drawn through anything else ===")
# Two defects that reached a real report and are easy to reintroduce:
#   * `.rd table` in the element reset is a class AND an element, so it
#     outranks a bare component class — every margin set on a table further
#     down the file was silently dropped. That put the masthead rule directly
#     on the facts card, slicing across its rounded corners.
#   * A section heading with its rule running out beside it lands the line on
#     the type's own x-height and reads as struck through the words.
doc = c.get(f"/r/{TOK}").data.decode()
chk("tables are not in the element margin reset",
    ".rd table, .rd figure" not in doc and ".rd table," not in doc.split("border-collapse")[0],
    "the reset would override every table margin set below it")
chk("the facts card keeps its distance from the masthead rule",
    re.search(r"\.facts \{[^}]*margin-top:\s*(1[5-9]|2[0-9])px", doc), "gap too small or unset")
chk("section headings rule underneath, not beside",
    re.search(r"\.sec \{[^}]*border-bottom", doc) and '<div class="sec">' in doc,
    "heading rule is back beside the text")
chk("no heading is laid out as text-plus-rule cells",
    'class="rule"' not in doc)
chk("the observations are spaced apart",
    re.search(r"\.obs \{[^}]*margin-bottom", doc))

print("\n=== 6. what is viewed is what is printed ===")
web=c.get(f"/r/{TOK}").data.decode()
sheets_web=len(re.findall(r'<section class="sheet"', web))
with APP.test_request_context("/"):
    from flask import render_template
    import pdf_gen
    fl=db.session.get(Flight,FID)
    html=render_template("report_print.html",pdf=True,public=True,share=None,
        logo_uri="",map_uri="",**appmod.report_context(fl))
sheets_pdf=len(re.findall(r'<section class="sheet"', html))
chk("same number of A4 sheets in both", sheets_web==sheets_pdf, f"web={sheets_web} pdf={sheets_pdf}")
chk("both are built from the one document",
    all(("size: A4" in doc and "@bottom-right" in doc and 'class="sheet"' in doc)
        for doc in (web, html)))
chk("no Tailwind utility classes inside the document",
    'class="sheet"' in web and "text-[13px]" not in web.split('<section class="sheet"')[1])
chk("print CSS drops the app chrome", "@media print" in web and "no-print" in web)
chk("backgrounds survive the browser print path", "print-color-adjust:exact" in web)
chk("fonts are bundled, not fetched from a CDN",
    "montserrat-latin-800-normal.woff2" in web and "fonts.googleapis.com" not in web.split("<style>")[1].split("</style>")[0])

print("\n=== 7. degrading gracefully ===")
with APP.app_context():
    bare=Flight(farm_id=1,season="2026SR",flight_number=1,flights_planned=1)
    db.session.add(bare); db.session.commit()
    import report_data
    an=report_data.analyse(bare)
    chk("a flight with no findings still analyses", an["score"]==100, an["score"])
    chk("and still paginates to one sheet", len(an["pages"])==1, len(an["pages"]))
    r=c2.get(f"/flights/{bare.id}/report")
    chk("and still renders", r.status_code==200, r.status_code)

print(f"\n{'='*60}\n  {P} passed, {F} failed\n{'='*60}")
sys.exit(1 if F else 0)
