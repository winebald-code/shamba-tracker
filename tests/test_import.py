"""
Checks for the bulk import of farms and flights.

Covers reading CSV and Excel, tolerant column headings, matching an existing
record versus creating a new one, the rule that a blank cell never wipes a
value, and that a bad row is reported without stopping the rest of the sheet.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_import.py
"""
import os, sys, tempfile, io, csv, re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"i.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")
import app as appmod
appmod.app.config["TESTING"] = True
from models import db, Farm, Flight
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d)))
    P,F=(P+1,F) if c else (P,F+1)

c=appmod.app.test_client()
c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

def upload(kind, content, name):
    return c.post(f"/import/{kind}/preview",
        data={"sheet": (io.BytesIO(content if isinstance(content,bytes) else content.encode()), name)},
        content_type="multipart/form-data", follow_redirects=True)

def payload_from(html):
    m=re.search(r'name="payload" value="(.*?)">', html, re.S)
    import html as H
    return H.unescape(m.group(1)) if m else None

def apply_(kind, html):
    return c.post(f"/import/{kind}/apply", data={"payload": payload_from(html)}, follow_redirects=True)

print("\n=== 1. farms: create from CSV, headings in mixed styles ===")
farms_csv = ("Farm Name,crop,ACREAGE,Location,farmer name,Email,WhatsApp,DroneDeploy URL,notes\n"
             "Kilimo Bora Farm,Maize,42,\"Naromoru, Kenya\",John Mwangi,john@ex.com,+254712345678,https://dd.com/a,first\n"
             "Green Ridge,Potatoes,18.5,Nyeri,Mary W,mary@ex.com,0722000111,,\n"
             "Hilltop Shamba,Beans,7,Meru,,,,,\n")
r=upload("farms", farms_csv, "farms.csv")
chk("preview renders", r.status_code==200, r.status_code)
chk("3 to add", "to add" in r.data.decode())
r2=apply_("farms", r.data.decode())
with appmod.app.app_context():
    names=sorted(f.name for f in Farm.query.all())
    chk("all three farms created", names==["Green Ridge","Hilltop Shamba","Kilimo Bora Farm"], names)
    kb=Farm.query.filter_by(name="Kilimo Bora Farm").first()
    chk("acreage parsed", kb.acreage==42.0, kb.acreage)
    chk("location with a comma survived", kb.location=="Naromoru, Kenya", kb.location)
    chk("odd heading 'WhatsApp' mapped to phone", kb.farmer_phone=="+254712345678", kb.farmer_phone)
    chk("odd heading 'Email' mapped", kb.farmer_email=="john@ex.com", kb.farmer_email)
    chk("decimal acreage parsed", Farm.query.filter_by(name="Green Ridge").first().acreage==18.5)

print("\n=== 2. farms: update, and blanks must not wipe ===")
upd = ("name,farmer_phone\n"
       "Kilimo Bora Farm,+254799888777\n"
       "Green Ridge,\n")
r=upload("farms", upd, "u.csv"); h=r.data.decode()
chk("both seen as updates", "2</p>" in h.replace(" ","") or "to update" in h)
apply_("farms", h)
with appmod.app.app_context():
    kb=Farm.query.filter_by(name="Kilimo Bora Farm").first()
    gr=Farm.query.filter_by(name="Green Ridge").first()
    chk("phone updated", kb.farmer_phone=="+254799888777", kb.farmer_phone)
    chk("crop NOT wiped by absent column", kb.crop=="Maize", kb.crop)
    chk("location NOT wiped by absent column", kb.location=="Naromoru, Kenya", kb.location)
    chk("blank cell left the old phone alone", gr.farmer_phone=="0722000111", gr.farmer_phone)
    chk("no duplicate farm created", Farm.query.count()==3, Farm.query.count())

print("\n=== 3. farms: bad rows are reported, good rows still import ===")
bad = ("name,acreage\n"
       "Valley Farm,notanumber\n"
       ",12\n"
       "Kilimo Bora Farm,50\n"
       "Ridge Two,9\n"
       "Ridge Two,10\n")
r=upload("farms", bad, "b.csv"); h=r.data.decode()
chk("acreage error named", "is not a number" in h)
chk("missing name flagged", "No farm name in this row" in h)
chk("in-file duplicate flagged", "appears earlier in this file" in h)
apply_("farms", h)
with appmod.app.app_context():
    chk("good rows landed", Farm.query.filter_by(name="Ridge Two").first() is not None)
    chk("bad row skipped", Farm.query.filter_by(name="Valley Farm").first() is None)
    chk("existing farm still updated", Farm.query.filter_by(name="Kilimo Bora Farm").first().acreage==50.0)

print("\n=== 4. flights: create from CSV ===")
fl = ("Farm,Season,Flight No,Flights Planned,Crop,Acreage,Date,Status\n"
      "Kilimo Bora Farm,2026LR,1,12,Maize,42,2026-03-14,Draft\n"
      "Kilimo Bora Farm,2026LR,2,12,Maize,42,21/03/2026,Ready for Review\n"
      "Green Ridge,2026LR,1,6,Potatoes,18.5,2026-04-02,\n")
r=upload("flights", fl, "f.csv"); h=r.data.decode()
chk("flights preview renders", r.status_code==200)
apply_("flights", h)
with appmod.app.app_context():
    chk("3 flights created", Flight.query.count()==3, Flight.query.count())
    f1=Flight.query.filter_by(season="2026LR",flight_number=1).first()
    chk("ISO date parsed", str(f1.flight_date)=="2026-03-14", f1.flight_date)
    f2=Flight.query.filter_by(season="2026LR",flight_number=2).first()
    chk("dd/mm/yyyy date parsed", str(f2.flight_date)=="2026-03-21", f2.flight_date)
    chk("status honoured", f2.status=="Ready for Review", f2.status)
    gr=Flight.query.join(Farm).filter(Farm.name=="Green Ridge").first()
    chk("blank status defaults to Draft", gr.status=="Draft", gr.status)
    chk("share token minted on import", bool(f1.share_token), f1.share_token)

print("\n=== 5. flights: update by farm+season+number, no duplicates ===")
upd2 = ("farm,season,flight number,status\n"
        "Kilimo Bora Farm,2026LR,1,Approved\n")
r=upload("flights", upd2, "f2.csv"); h=r.data.decode()
chk("recognised as an update", "Update" in h)
apply_("flights", h)
with appmod.app.app_context():
    chk("still 3 flights", Flight.query.count()==3, Flight.query.count())
    f1=Flight.query.filter_by(season="2026LR",flight_number=1).first()
    chk("status updated", f1.status=="Approved", f1.status)
    chk("date not wiped", str(f1.flight_date)=="2026-03-14", f1.flight_date)
    chk("crop not wiped", f1.crop=="Maize", f1.crop)

print("\n=== 6. flights: unknown farm is refused, not guessed ===")
r=upload("flights", "farm,season,flight number\nNo Such Farm,2026LR,1\n", "x.csv")
h=r.data.decode()
chk("unknown farm flagged", "No farm called" in h)
apply_("flights", h)
with appmod.app.app_context():
    chk("nothing created for it", Flight.query.count()==3, Flight.query.count())

print("\n=== 7. xlsx round-trip ===")
from openpyxl import Workbook
wb=Workbook(); ws=wb.active
ws.append(["Farm name","Crop","Acreage","Location"])
ws.append(["Excel Farm","Sorghum",25.0,"Kitui"])
ws.append(["Kilimo Bora Farm","Maize",42.0,"Naromoru, Kenya"])
buf=io.BytesIO(); wb.save(buf)
r=upload("farms", buf.getvalue(), "farms.xlsx"); h=r.data.decode()
chk("xlsx preview renders", r.status_code==200)
apply_("farms", h)
with appmod.app.app_context():
    ef=Farm.query.filter_by(name="Excel Farm").first()
    chk("xlsx row created", ef is not None)
    chk("float 25.0 read as 25", ef.acreage==25.0, ef.acreage)

print("\n=== 8. templates download ===")
for kind in ("farms","flights"):
    r=c.get(f"/import/template/{kind}.csv")
    chk(f"{kind} template downloads", r.status_code==200, r.status_code)
    rows=list(csv.reader(io.StringIO(r.data.decode())))
    chk(f"{kind} template has a heading + sample row", len(rows)==2, len(rows))
    # the template's own headings must be readable by the importer
    r2=upload(kind, r.data, f"{kind}.csv")
    chk(f"{kind} template re-imports cleanly", b"to skip" in r2.data and b"Skip" not in r2.data,
        "template headings must map")

print("\n=== 9. guards ===")
r=c.post("/import/farms/preview", data={}, content_type="multipart/form-data", follow_redirects=True)
chk("no file is handled", r.status_code==200 and b"Choose a CSV" in r.data)
r=upload("farms", b"nope", "notes.txt")
chk("wrong extension refused", b"can&#39;t be read" in r.data or b"can't be read" in r.data)
r=upload("farms", "colour,size\nred,big\n", "wrong.csv")
chk("sheet with no name column refused", b"No farm name column found" in r.data)
r=c.post("/import/farms/apply", data={"payload":"garbage"}, follow_redirects=True)
chk("tampered payload refused", b"expired" in r.data)
r=c.get("/import/template/nope.csv")
chk("unknown template 404s", r.status_code==404, r.status_code)

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
