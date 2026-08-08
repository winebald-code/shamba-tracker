"""
Checks for the farmer comment on a finding.

Customer success records what the farmer said back, usually when the farmer's
own knowledge of the field disagrees with what was reported. It is internal: the
report is the record of what was advised, and a farmer disputing a finding is a
note about that advice rather than part of it.

The suite covers who may record one, that it never reaches any report surface,
that recording one does not invalidate a report already generated and sent, and
that clearing it also clears its attribution.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_farmer_comment.py
"""
import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "fc.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")

import app as appmod
from models import db, Farm, Flight, Finding, User
from datetime import date

APP = appmod.app
APP.config["TESTING"] = True
P = F = 0
SENTINEL = "SENTINEL-FARMER-DISPUTE-42"


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label + ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


with APP.app_context():
    farm = Farm(name="Comment Farm", crop="Maize", acreage=12.0)
    db.session.add(farm)
    db.session.commit()
    flight = Flight(farm_id=farm.id, season="2026LR", flight_number=1, flights_planned=4,
                    crop="Maize", acreage=12.0, flight_date=date(2026, 3, 4))
    db.session.add(flight)
    db.session.commit()
    finding = Finding(flight_id=flight.id, category="Pest / Disease",
                      colour_meaning="Needs testing", colour_swatch="#D64550",
                      annotation_link="https://www.dronedeploy.com/app2/x",
                      observation="Yellowing across the north block.",
                      likely_cause="Nitrogen deficiency.",
                      recommendation="Top dress with CAN before the next rains.",
                      area_text="2.0 ac", area_acres=2.0)
    db.session.add(finding)
    db.session.commit()
    FID, FLID, TOKEN = finding.id, flight.id, flight.share_token

    cs = User(name="Cee Ess", email="cs@a.com", role="customer_success", status="approved")
    cs.set_password("password123")
    db.session.add(cs)
    db.session.commit()

print("\n=== 1. a finding starts with no comment ===")
with APP.app_context():
    f = db.session.get(Finding, FID)
    chk("no comment on a new finding", not f.has_farmer_comment)
    chk("the flight counts none", db.session.get(Flight, FLID).farmer_comment_count == 0)
    chk("completeness ignores the comment", f.is_complete)

print("\n=== 2. customer success may record one ===")
cs_client = APP.test_client()
cs_client.post("/login", data={"email": "cs@a.com", "password": "password123"}, follow_redirects=True)
r = cs_client.post(f"/findings/{FID}/comment",
                   json={"farmer_comment": "Farmer says that block was replanted late, not deficient."})
chk("the request is accepted", r.status_code == 200, r.status_code)
d = r.get_json()
chk("the response confirms it saved", d["has_comment"] is True, d)
chk("the flight count is returned", d["flagged"] == 1, d)
chk("the recorder is named", d["recorded_by"] == "Cee Ess", d)
with APP.app_context():
    f = db.session.get(Finding, FID)
    chk("the text persisted", "replanted late" in f.farmer_comment, f.farmer_comment)
    chk("the time is stamped", f.farmer_comment_at is not None)
    chk("the recorder is stored", f.farmer_comment_by_id is not None)

print("\n=== 3. recording one does not invalidate a generated report ===")
with APP.app_context():
    fl = db.session.get(Flight, FLID)
    fl.report_generated = True
    db.session.commit()
cs_client.post(f"/findings/{FID}/comment", json={"farmer_comment": "Updated after a second call."})
with APP.app_context():
    chk("the report stays generated", db.session.get(Flight, FLID).report_generated is True)

print("\n=== 4. customer success still cannot edit the finding ===")
r = cs_client.post(f"/findings/{FID}/update", json={"observation": "tampered"})
chk("editing the finding is refused", r.status_code in (302, 403), r.status_code)
with APP.app_context():
    chk("the observation is untouched",
        db.session.get(Finding, FID).observation == "Yellowing across the north block.")

print("\n=== 5. clearing the comment clears its attribution ===")
cs_client.post(f"/findings/{FID}/comment", json={"farmer_comment": "   "})
with APP.app_context():
    f = db.session.get(Finding, FID)
    chk("the comment is empty", not f.has_farmer_comment)
    chk("the timestamp is cleared", f.farmer_comment_at is None)
    chk("the recorder is cleared", f.farmer_comment_by_id is None)
    chk("the flight count is back to zero", db.session.get(Flight, FLID).farmer_comment_count == 0)

print("\n=== 6. it never reaches any report surface ===")
with APP.app_context():
    f = db.session.get(Finding, FID)
    f.farmer_comment = SENTINEL
    db.session.commit()

admin = APP.test_client()
admin.post("/login", data={"email": "a@a.com", "password": "password123"}, follow_redirects=True)
chk("absent from the internal web report",
    SENTINEL not in admin.get(f"/flights/{FLID}/report").data.decode())
chk("absent from the farmer's public report",
    SENTINEL not in APP.test_client().get(f"/r/{TOKEN}").data.decode())
pdf = admin.get(f"/flights/{FLID}/report.pdf")
chk("absent from the downloaded report bytes", SENTINEL.encode() not in pdf.data)
chk("absent from the farmer's downloaded report",
    SENTINEL.encode() not in APP.test_client().get(f"/r/{TOKEN}/report.pdf").data)
chk("but present in the internal portal",
    SENTINEL in admin.get(f"/flights/{FLID}").data.decode())

print("\n=== 7. the review row keeps its shape ===")
page = admin.get(f"/flights/{FLID}").data.decode()
chk("the internal badge is gone", "not in the report" not in page)
chk("the comment box is still there", "data-comment=" in page)
# The grid places children in source order, so the actions must come first to
# stay in the finding's own row rather than being pushed below the comment.
i_actions, i_comment = page.find("DESKTOP actions"), page.find("Farmer comment")
chk("the actions render before the comment", 0 < i_actions < i_comment, (i_actions, i_comment))
chk("the annotation link is still in the row", "dronedeploy" in page.lower())
chk("the delete control is still in the row", "/delete" in page)

print("\n=== 8. an agronomist and an admin may also record one ===")
with APP.app_context():
    ag = User(name="Aggie", email="ag2@a.com", role="agronomist", status="approved")
    ag.set_password("password123")
    op = User(name="Ollie", email="op2@a.com", role="field_operator", status="approved")
    op.set_password("password123")
    db.session.add_all([ag, op])
    db.session.commit()
ag_client = APP.test_client()
ag_client.post("/login", data={"email": "ag2@a.com", "password": "password123"}, follow_redirects=True)
chk("an agronomist may record one",
    ag_client.post(f"/findings/{FID}/comment", json={"farmer_comment": "Agreed with the farmer."}).status_code == 200)
op_client = APP.test_client()
op_client.post("/login", data={"email": "op2@a.com", "password": "password123"}, follow_redirects=True)
chk("a field operator may not",
    op_client.post(f"/findings/{FID}/comment", json={"farmer_comment": "x"}).status_code in (302, 403))
chk("anonymous may not",
    APP.test_client().post(f"/findings/{FID}/comment", json={"farmer_comment": "x"}).status_code in (302, 401))

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
