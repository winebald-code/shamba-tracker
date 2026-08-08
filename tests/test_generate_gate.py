"""
Checks for the two gates that decide what a user can reach.

A report needs the annotated map as well as complete findings: the map is what
the farmer reads first and what the numbered findings point back at, so a report
without it is a list of pin numbers referring to nothing. When generation is
blocked, the page has to say which of the two is missing rather than blaming the
findings by default.

And a signed-in admin has to be able to open the public homepage to see what a
visitor sees, without being bounced to their dashboard and without signing out.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_generate_gate.py
"""
import os, sys, tempfile, shutil, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "gate.db"), SECRET_KEY="t",
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


def banner(page):
    """The banner as rendered, not the JavaScript that can also rewrite it."""
    m = re.search(r'<p id="bannerText"[^>]*>(.*?)</p>', page, re.S)
    return " ".join(m.group(1).split()) if m else ""


with APP.app_context():
    farm = Farm(name="Gate Farm", crop="Maize", acreage=9.0)
    db.session.add(farm)
    db.session.commit()
    flight = Flight(farm_id=farm.id, season="2026LR", flight_number=1, flights_planned=2,
                    crop="Maize", acreage=9.0, flight_date=date(2026, 3, 1))
    db.session.add(flight)
    db.session.commit()
    db.session.add(Finding(flight_id=flight.id, category="Pest / Disease",
                           colour_meaning="Needs testing", colour_swatch="#D64550",
                           observation="Reduced growth along one line.",
                           likely_cause="Drip malfunction.",
                           recommendation="Inspect the drip line.", area_acres=2.0))
    db.session.commit()
    FLID = flight.id

admin = APP.test_client()
admin.post("/login", data={"email": "a@a.com", "password": "password123"}, follow_redirects=True)

print("\n=== 1. complete findings are not enough on their own ===")
with APP.app_context():
    fl = db.session.get(Flight, FLID)
    chk("every finding is complete", fl.incomplete_count == 0)
    chk("but the report cannot be generated without the map", not fl.can_generate)
page = admin.get(f"/flights/{FLID}").data.decode()
chk("the banner asks for the map", "Upload the annotated map snapshot" in banner(page), banner(page))
chk("the banner does not claim readiness", "You can generate" not in banner(page), banner(page))
chk("the generate button is disabled", "disabled" in page)

print("\n=== 2. generating is refused, and says why ===")
r = admin.post(f"/flights/{FLID}/generate", follow_redirects=True)
chk("the map is named as the blocker", b"Upload the annotated map snapshot first" in r.data)
chk("the findings are not blamed", b"Complete every finding first" not in r.data)
with APP.app_context():
    chk("nothing was generated", db.session.get(Flight, FLID).report_generated is False)
chk("sending is refused too",
    admin.post(f"/flights/{FLID}/send", follow_redirects=True).status_code == 200)
with APP.app_context():
    chk("still not generated", db.session.get(Flight, FLID).report_generated is False)

print("\n=== 3. with the map, generation works ===")
shutil.copy(os.path.join(ROOT, "samples", "sample_annotated_map.jpg"),
            os.path.join(appmod.UPLOAD_DIR, "gate.jpg"))
with APP.app_context():
    fl = db.session.get(Flight, FLID)
    fl.map_image = "gate.jpg"
    db.session.commit()
    chk("the flight can now generate", fl.can_generate)
page = admin.get(f"/flights/{FLID}").data.decode()
chk("the banner confirms both are done", "and the map is uploaded" in banner(page), banner(page))
admin.post(f"/flights/{FLID}/generate", follow_redirects=True)
with APP.app_context():
    chk("the report is generated", db.session.get(Flight, FLID).report_generated is True)

print("\n=== 4. an incomplete finding is still blamed correctly ===")
with APP.app_context():
    db.session.get(Flight, FLID).findings[0].recommendation = ""
    db.session.commit()
r = admin.post(f"/flights/{FLID}/generate", follow_redirects=True)
chk("the findings are named as the blocker", b"Complete every finding first" in r.data)
chk("the map is not blamed", b"Upload the annotated map snapshot first" not in r.data)
with APP.app_context():
    db.session.get(Flight, FLID).findings[0].recommendation = "Inspect the drip line."
    db.session.commit()

print("\n=== 5. a flight with no findings is unchanged ===")
with APP.app_context():
    farm = db.session.get(Farm, 1)
    bare = Flight(farm_id=farm.id, season="2026LR", flight_number=2, flights_planned=2,
                  crop="Maize", acreage=9.0, flight_date=date(2026, 3, 8), map_image="gate.jpg")
    db.session.add(bare)
    db.session.commit()
    BID = bare.id
    chk("a map alone does not make it generatable", not bare.can_generate)
chk("the banner still asks for findings",
    "Import a DroneDeploy CSV" in banner(admin.get(f"/flights/{BID}").data.decode()))

print("\n=== 6. an admin can view the public homepage while signed in ===")
r = admin.get("/")
chk("plain / still sends a signed-in user to their dashboard",
    r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""),
    (r.status_code, r.headers.get("Location")))
r = admin.get("/?preview=1")
chk("preview renders the homepage instead", r.status_code == 200, r.status_code)
chk("and it is the real homepage", "The flight lands at noon." in r.data.decode())
editor = admin.get("/homepage").data.decode()
chk("the View homepage button uses it", "preview=1" in editor)
chk("and opens in a new tab", 'target="_blank"' in editor)
chk("an anonymous visitor still gets the homepage at /",
    APP.test_client().get("/").status_code == 200)

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
