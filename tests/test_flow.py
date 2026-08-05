"""
End-to-end check of the SHAMBA Tracker request flow.

Runs against a throwaway SQLite file with a real Flask test client, so every
template actually renders and every route is really dispatched. Run it with:

    python tests/test_flow.py

It exits non-zero on the first failure and prints a one-line summary per check.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_EMAIL"] = "cathy@acre-insights.com"
os.environ["ADMIN_PASSWORD"] = "AcreInsights2026"
os.environ["PUBLIC_BASE_URL"] = "https://shamba-tracker.winebald.tech"

import app as appmod                       # noqa: E402
from models import db, User, Farm, Flight, Finding   # noqa: E402
import integrations                        # noqa: E402

APP = appmod.app
APP.config["TESTING"] = True
APP.config["WTF_CSRF_ENABLED"] = False

PASSED, FAILED = [], []


def check(label, condition, detail=""):
    (PASSED if condition else FAILED).append(label)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail and not condition else ""))


def login(client, email, pw):
    return client.post("/login", data={"email": email, "password": pw}, follow_redirects=True)


def run():
    print("\n=== 1. signup requires approval ===")
    with APP.test_client() as c:
        r = c.get("/signup")
        check("signup page renders", r.status_code == 200, r.status_code)
        r = c.post("/signup", data={
            "name": "Joy Achieng", "email": "joy@acre-insights.com",
            "password": "fieldwork2026", "requested_role": "agronomist",
            "job_title": "Agronomist", "phone": "0712345678"}, follow_redirects=True)
        check("signup lands on the waiting page", b"with the admin" in r.data, r.status_code)

    with APP.app_context():
        joy = User.query.filter_by(email="joy@acre-insights.com").first()
        check("new account is pending", joy is not None and joy.status == "pending",
              joy.status if joy else "missing")
        check("pending account cannot hold a session", joy is not None and not joy.is_active)

    with APP.test_client() as c:
        r = login(c, "joy@acre-insights.com", "fieldwork2026")
        check("pending user is bounced to the waiting page", b"with the admin" in r.data)
        r = c.get("/dashboard", follow_redirects=True)
        check("pending user cannot reach a dashboard", b"Sign in" in r.data or b"sign in" in r.data)

    print("\n=== 2. admin approves, role is applied ===")
    with APP.test_client() as c:
        r = login(c, "cathy@acre-insights.com", "AcreInsights2026")
        check("seeded admin signs in", r.status_code == 200)
        r = c.get("/dashboard", follow_redirects=True)
        check("admin lands on the admin dashboard", b"Admin dashboard" in r.data)
        r = c.get("/users")
        check("approval queue lists the request", b"joy@acre-insights.com" in r.data)
        with APP.app_context():
            uid = User.query.filter_by(email="joy@acre-insights.com").first().id
        r = c.post(f"/users/{uid}/approve", data={"role": "agronomist"}, follow_redirects=True)
        check("approval succeeds", r.status_code == 200)

    with APP.app_context():
        joy = User.query.filter_by(email="joy@acre-insights.com").first()
        check("account is now approved", joy.status == "approved", joy.status)
        check("role was applied", joy.role == "agronomist", joy.role)
        check("approver was recorded", joy.approved_by_id is not None)

    print("\n=== 3. every role gets its own dashboard ===")
    with APP.app_context():
        for name, email, role in [("Sam Otieno", "sam@acre-insights.com", "customer_success"),
                                  ("Ken Mwangi", "ken@acre-insights.com", "field_operator")]:
            u = User(name=name, email=email, role=role, requested_role=role,
                     status="approved", active=True)
            u.set_password("fieldwork2026")
            db.session.add(u)
        db.session.commit()

    expected = {
        "cathy@acre-insights.com": (b"Admin dashboard", "/dashboard/admin"),
        "joy@acre-insights.com": (b"Your review queue", "/dashboard/agronomist"),
        "sam@acre-insights.com": (b"Reports and delivery", "/dashboard/customer-success"),
        "ken@acre-insights.com": (b"Field operations", "/dashboard/field"),
    }
    pw = {"cathy@acre-insights.com": "AcreInsights2026"}
    for email, (marker, path) in expected.items():
        with APP.test_client() as c:
            login(c, email, pw.get(email, "fieldwork2026"))
            r = c.get("/dashboard", follow_redirects=True)
            check(f"{email.split('@')[0]} sees their own dashboard", marker in r.data, r.status_code)
            r = c.get(path)
            check(f"{path} renders directly", r.status_code == 200, r.status_code)

    print("\n=== 4. role permissions are enforced ===")
    with APP.test_client() as c:
        login(c, "sam@acre-insights.com", "fieldwork2026")
        check("non-admin is refused the people page", c.get("/users").status_code == 403)
        check("non-admin is refused the admin dashboard",
              c.get("/dashboard/admin").status_code == 403)
        check("customer success cannot create a farm",
              c.post("/farms/new", data={"name": "X"}).status_code == 403)
    with APP.test_client() as c:
        login(c, "ken@acre-insights.com", "fieldwork2026")
        check("field operator can create a farm",
              c.post("/farms/new", data={"name": "Mashuru Onion Farm", "crop": "Onions",
                                         "acreage": "10.72", "location": "Kajiado",
                                         "farmer_name": "Peter Sankale",
                                         "farmer_email": "peter@example.com",
                                         "farmer_phone": "0722334455"},
                     follow_redirects=True).status_code == 200)

    print("\n=== 5. profile CRUD ===")
    with APP.test_client() as c:
        login(c, "joy@acre-insights.com", "fieldwork2026")
        r = c.get("/profile")
        check("profile page renders", r.status_code == 200 and b"Your details" in r.data)
        r = c.post("/profile/edit", data={
            "name": "Joy A. Achieng", "email": "joy@acre-insights.com",
            "job_title": "Senior Agronomist", "phone": "+254712345678",
            "location": "Nairobi, Kenya", "bio": "Onions and potatoes across Kajiado."},
            follow_redirects=True)
        check("profile update succeeds", r.status_code == 200)
        with APP.app_context():
            joy = User.query.filter_by(email="joy@acre-insights.com").first()
            check("profile fields persisted",
                  joy.name == "Joy A. Achieng" and joy.job_title == "Senior Agronomist",
                  f"{joy.name} / {joy.job_title}")
        r = c.post("/profile/password", data={"current_password": "wrong",
                                              "new_password": "newpass2026",
                                              "confirm_password": "newpass2026"},
                   follow_redirects=True)
        check("wrong current password is rejected", b"isn" in r.data)
        r = c.post("/profile/password", data={"current_password": "fieldwork2026",
                                              "new_password": "newpass2026",
                                              "confirm_password": "newpass2026"},
                   follow_redirects=True)
        check("password change succeeds", b"Password changed" in r.data)
    with APP.test_client() as c:
        r = login(c, "joy@acre-insights.com", "newpass2026")
        check("new password signs in", b"Your review queue" in r.data)

    print("\n=== 6. the only admin is protected ===")
    with APP.test_client() as c:
        login(c, "cathy@acre-insights.com", "AcreInsights2026")
        with APP.app_context():
            cid = User.query.filter_by(email="cathy@acre-insights.com").first().id
        r = c.post("/profile/delete", data={"confirm": "cathy@acre-insights.com"},
                   follow_redirects=True)
        check("last admin cannot delete themselves", b"only admin" in r.data)
        r = c.post(f"/users/{cid}/toggle", follow_redirects=True)
        check("admin cannot deactivate themselves", b"your own account" in r.data)

    print("\n=== 7. report share links ===")
    with APP.app_context():
        farm = Farm.query.first()
        fl = Flight(farm_id=farm.id, season="2026LR", flight_number=1, flights_planned=12,
                    crop="Onions", acreage=10.72, status="Draft")
        db.session.add(fl)
        db.session.commit()
        db.session.add(Finding(flight_id=fl.id, category="Irrigation",
                               colour_meaning="Needs testing", colour_swatch="#D64550",
                               observation="Stress along the eastern feeder line.",
                               likely_cause="Pressure shortfall at the far bay.",
                               recommendation="Check the feeder pressure before the next irrigation.",
                               sort_order=0, annotation_id="a1"))
        db.session.commit()
        fid = fl.id
        links = integrations.share_links(fl, "https://shamba-tracker.winebald.tech")
        check("whatsapp link is a wa.me deep link", links["whatsapp"].startswith("https://wa.me/"),
              links["whatsapp"][:60])
        check("kenyan trunk zero becomes a country code",
              links["phone_e164"] == "254722334455", links["phone_e164"])
        check("mailto carries a subject and body",
              links["mailto"].startswith("mailto:peter@example.com?") and "subject=" in links["mailto"])
        check("gmail compose link is built",
              links["gmail"].startswith("https://mail.google.com/mail/?"))
        check("share link uses the public base",
              links["link"].startswith("https://shamba-tracker.winebald.tech/r/"), links["link"])

    with APP.test_client() as c:
        login(c, "cathy@acre-insights.com", "AcreInsights2026")
        r = c.get(f"/flights/{fid}/report")
        check("report page renders", r.status_code == 200, r.status_code)
        check("share panel shows the WhatsApp button", b"Send from my WhatsApp" in r.data)
        check("share panel shows the email button", b"Send from my email" in r.data)
        r = c.post(f"/flights/{fid}/mark-shared", json={"channel": "whatsapp"})
        check("hand-off share is recorded", r.status_code == 200 and r.get_json()["ok"])
        r = c.post(f"/flights/{fid}/mark-shared", json={"channel": "carrier-pigeon"})
        check("unknown channel is rejected", r.status_code == 400)

    with APP.app_context():
        fl = db.session.get(Flight, fid)
        check("flight is marked sent by whatsapp",
              fl.sent_whatsapp and fl.status == "Sent" and fl.delivery_method == "handoff",
              f"{fl.sent_whatsapp}/{fl.status}/{fl.delivery_method}")
        token = fl.share_token

    print("\n=== 8. public report and acknowledgement ===")
    with APP.test_client() as c:
        r = c.get(f"/r/{token}")
        check("public report opens without a login", r.status_code == 200, r.status_code)
        check("public report hides the share panel", b"Send from my WhatsApp" not in r.data)
        r = c.post(f"/r/{token}/ack")
        check("farmer can acknowledge", r.status_code == 200 and r.get_json()["ok"])

    print("\n=== 9. public pages and error handling ===")
    with APP.test_client() as c:
        r = c.get("/")
        check("home page renders", r.status_code == 200)
        check("home has a hamburger menu", b'id="navToggle"' in r.data and b'id="navdrawer"' in r.data)
        check("home links every section", all(x in r.data for x in [b'id="how"', b'id="code"',
                                                                    b'id="deliver"', b'id="report"']))
        check("404 renders the error page", c.get("/nope").status_code == 404)
        check("pending page renders standalone", c.get("/pending").status_code == 200)

    print("\n=== 10. every registered route responds ===")
    skip_prefix = ("/static", "/uploads")
    with APP.test_client() as c:
        login(c, "cathy@acre-insights.com", "AcreInsights2026")
        broken = []
        for rule in APP.url_map.iter_rules():
            if "GET" not in rule.methods or rule.arguments or str(rule).startswith(skip_prefix):
                continue
            resp = c.get(str(rule))
            if resp.status_code >= 500:
                broken.append(f"{rule} -> {resp.status_code}")
        check("no GET route returns a server error", not broken, "; ".join(broken))

    print("\n" + "=" * 62)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for f in FAILED:
            print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
