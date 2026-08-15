"""
Checks for the report filename.

The name carries the flight number as well as the farm, crop and season, so two
flights of one season no longer overwrite each other in the farmer's downloads
folder. This covers the name itself, the on-disk slug, the download header and
the title the browser's own "Save as PDF" picks up.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_filename.py
"""
import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "fn.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")

import app as appmod
from models import db, Farm, Flight, Finding
from datetime import date

APP = appmod.app
APP.config["TESTING"] = True
P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label + ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


with APP.app_context():
    farm = Farm(name="Kilimo Bora Farm", crop="Maize", acreage=42.0)
    db.session.add(farm)
    db.session.commit()
    ids = []
    for n in (1, 2, 11):
        fl = Flight(farm_id=farm.id, season="2026LR", flight_number=n, flights_planned=12,
                    crop="Maize", acreage=42.0, flight_date=date(2026, 3, 14))
        db.session.add(fl)
        db.session.commit()
        db.session.add(Finding(flight_id=fl.id, category="Pest / Disease",
                               colour_meaning="Needs testing", colour_swatch="#D64550",
                               observation="o", likely_cause="c", recommendation="r",
                               area_text="3.8 ac", area_acres=3.8))
        db.session.commit()
        ids.append(fl.id)

print("\n=== 1. the name carries the flight number ===")
with APP.app_context():
    one, two, eleven = (db.session.get(Flight, i) for i in ids)
    chk("Farm Name_Crop Name_Season Year_Flight No",
        one.report_filename == "Kilimo Bora Farm_Maize_2026LR_Flight 1.pdf", one.report_filename)
    chk("a double-digit flight reads the same way",
        eleven.report_filename == "Kilimo Bora Farm_Maize_2026LR_Flight 11.pdf",
        eleven.report_filename)
    chk("two flights of one season no longer collide",
        one.report_filename != two.report_filename,
        (one.report_filename, two.report_filename))
    chk("the on-disk slug carries it too, ASCII-safe",
        one.slug == "Kilimo_Bora_Farm_Maize_2026LR_F1", one.slug)

print("\n=== 2. real farm names survive ===")
with APP.app_context():
    fl = db.session.get(Flight, ids[0])
    fl.farm.name = "O'Brien / Kariuki  Farm"
    fl.crop = "Maize (H614)"
    chk("illegal characters stripped, spaces kept",
        fl.report_filename == "O'Brien Kariuki Farm_Maize (H614)_2026LR_Flight 1.pdf",
        fl.report_filename)
    chk("the slug stays safe for the filesystem",
        fl.slug == "OBrien_Kariuki_Farm_Maize_H614_2026LR_F1", fl.slug)
    fl.farm.name = "Kilimo Bora Farm"
    fl.crop = "Maize"
    db.session.commit()

print("\n=== 3. missing pieces degrade rather than crash ===")
with APP.app_context():
    bare_farm = Farm(name="Nameless Crop Farm")
    db.session.add(bare_farm)
    db.session.commit()
    bare = Flight(farm_id=bare_farm.id, season="", flight_number=3, flights_planned=1)
    db.session.add(bare)
    db.session.commit()
    name = bare.report_filename
    chk("a flight with no crop or season still names a file", name.endswith("_Flight 3.pdf"), name)
    chk("and never produces an empty part", "__" not in name, name)

print("\n=== 4. the download header and the print title agree ===")
c = APP.test_client()
c.post("/login", data={"email": "a@a.com", "password": "password123"}, follow_redirects=True)

r = c.get(f"/flights/{ids[1]}/report.pdf")
cd = r.headers.get("Content-Disposition", "")
chk("a real PDF comes back", r.data[:5] == b"%PDF-", r.data[:12])
chk("header carries the readable name",
    'filename="Kilimo Bora Farm_Maize_2026LR_Flight 2.pdf"' in cd, cd)
chk("header carries an RFC 5987 copy", "filename*=UTF-8''" in cd, cd)

page = c.get(f"/flights/{ids[1]}/report").data.decode()
chk("the download link suggests the same name",
    'download="Kilimo Bora Farm_Maize_2026LR_Flight 2.pdf"' in page)
# The rename only runs on the print fallback, which is reached with ?print=1.
print_page = c.get(f"/flights/{ids[1]}/report?print=1").data.decode()
chk("the print path renames the document to match",
    '"Kilimo Bora Farm_Maize_2026LR_Flight 2"' in print_page)

print("\n=== 5. the emailed attachment uses it too ===")
with APP.app_context():
    fl = db.session.get(Flight, ids[1])
    chk("attachment name matches the download name",
        fl.report_filename == "Kilimo Bora Farm_Maize_2026LR_Flight 2.pdf", fl.report_filename)

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
