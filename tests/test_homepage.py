"""
Checks for admin-managed homepage content.

Covers the page rendering its shipped wording before anything is edited, an
admin's edits going live, the rule that only changed fields are stored, a field
returning to its default dropping its row, malformed list lines degrading rather
than breaking the page, and the public homepage surviving a database that has no
content table yet.

Runs against a throwaway SQLite file with a real Flask test client. Run it with:

    python tests/test_homepage.py
"""
import os, sys, tempfile, io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "hp.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")

import app as appmod
import homepage
from models import db, SiteContent, User

APP = appmod.app
APP.config["TESTING"] = True
P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label + ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


def defaults_for(*keys):
    return {k: homepage.DEFAULTS[k] for k in keys}


anon = APP.test_client()

print("\n=== 1. the shipped homepage renders before anything is edited ===")
r = anon.get("/")
chk("homepage returns 200", r.status_code == 200, r.status_code)
page = r.data.decode()
for txt in ["The flight lands at noon.", "The farmer reads it by three.",
            "Six steps, and the order matters", "Two ways out, and one of them always works",
            "Give your farmers a report worth reading.", "Nature meets intelligence",
            "Fly and upload", "Works on any phone or laptop", "Mashuru Onion Farm"]:
    chk(f"shipped copy present: {txt[:36]}", txt in page)
chk("meta description is rendered", 'name="description"' in page and "Turn a DroneDeploy" in page)
chk("every field has a default", len(homepage.DEFAULTS) == len(homepage.KINDS))

print("\n=== 2. only an admin may edit ===")
chk("anonymous is sent away", anon.get("/homepage").status_code in (302, 401))
with APP.app_context():
    u = User(name="Aggie", email="ag@a.com", role="agronomist", status="approved")
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
agro = APP.test_client()
agro.post("/login", data={"email": "ag@a.com", "password": "password123"}, follow_redirects=True)
chk("an agronomist is refused", agro.get("/homepage").status_code in (302, 403))
chk("an agronomist cannot save", agro.post("/homepage/save", data={"section": "hero"}).status_code in (302, 403))
admin = APP.test_client()
admin.post("/login", data={"email": "a@a.com", "password": "password123"}, follow_redirects=True)
chk("an admin gets the editor", admin.get("/homepage").status_code == 200)

print("\n=== 3. an edit reaches the live page ===")
hero_keys = ("hero_eyebrow", "hero_title_1", "hero_title_2", "hero_body",
             "hero_cta_primary", "hero_cta_secondary", "hero_stats", "hero_image")
form = defaults_for(*hero_keys)
form.update({"section": "hero", "hero_title_1": "Fly in the morning.",
             "hero_title_2": "Advice by lunchtime.", "hero_cta_primary": "Start free",
             "hero_stats": "9|farms live\n2|minutes to send"})
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("the new headline is live", "Fly in the morning." in page)
chk("the old headline is gone", "The flight lands at noon." not in page)
chk("the new button label is live", "Start free" in page)
chk("an edited list renders", "farms live" in page and "minutes to send" in page)
chk("an untouched section is unchanged", "Six steps, and the order matters" in page)

print("\n=== 4. only changed fields are stored ===")
with APP.app_context():
    keys = sorted(r.key for r in SiteContent.query.all())
    chk("an unchanged field stores no row", "hero_eyebrow" not in keys and "hero_image" not in keys, keys)
    chk("a changed field stores one", "hero_title_1" in keys and "hero_stats" in keys, keys)
    chk("attribution is recorded",
        all(r.updated_by_id for r in SiteContent.query.all()))

print("\n=== 5. returning a field to its default drops the row ===")
form = defaults_for(*hero_keys)
form["section"] = "hero"
admin.post("/homepage/save", data=form, follow_redirects=True)
with APP.app_context():
    chk("the rows are gone", SiteContent.query.count() == 0, SiteContent.query.count())
chk("the shipped headline is back", "The flight lands at noon." in anon.get("/").data.decode())

print("\n=== 6. a malformed list line costs one item, not the page ===")
form = defaults_for("how_eyebrow", "how_title", "how_body")
form.update({"section": "how", "how_steps": "Only a title\nSecond|With a description\n\n   \n"})
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("a line with no separator still renders", "Only a title" in page)
chk("a well-formed line still renders", "With a description" in page)
chk("blank lines are dropped", anon.get("/").status_code == 200)
chk("split_lines pads a short line", homepage.split_lines("a", 3) == [("a", "", "")],
    homepage.split_lines("a", 3))
chk("split_lines truncates a long one", homepage.split_lines("a|b|c|d", 2) == [("a", "b")],
    homepage.split_lines("a|b|c|d", 2))

print("\n=== 7. saving one section leaves the others alone ===")
form = defaults_for("cta_title", "cta_body", "cta_primary", "cta_secondary", "cta_image")
form.update({"section": "cta", "cta_title": "A different closing line."})
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("the new CTA is live", "A different closing line." in page)
chk("the earlier edit survived", "Only a title" in page)

print("\n=== 8. content is escaped, never executed ===")
form = defaults_for("cta_body", "cta_primary", "cta_secondary", "cta_image")
form.update({"section": "cta", "cta_title": "<script>alert(1)</script>"})
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("a script tag is escaped", "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page)

print("\n=== 9. reset returns everything to the shipped wording ===")
admin.post("/homepage/reset", follow_redirects=True)
with APP.app_context():
    chk("every saved row is cleared", SiteContent.query.count() == 0)
page = anon.get("/").data.decode()
chk("the shipped homepage is back",
    "The flight lands at noon." in page and "Six steps, and the order matters" in page)

print("\n=== 10. image upload replaces one image ===")
png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")
r = admin.post("/homepage/image/hero_image",
               data={"image": (io.BytesIO(png), "new-hero.png")},
               content_type="multipart/form-data", follow_redirects=True)
chk("the upload is accepted", r.status_code == 200)
with APP.app_context():
    row = SiteContent.query.filter_by(key="hero_image").first()
    # Uploaded images go to the store rather than static/, because static/ is
    # part of the container image and is rebuilt on every deploy.
    chk("the path is stored against the store", row is not None
        and row.value == "uploads/home-hero-image.png", row.value if row else None)
written = os.path.join(appmod.UPLOAD_DIR, "home-hero-image.png")
chk("the file is in the store", os.path.exists(written))
chk("the homepage points at it", "home-hero-image.png" in anon.get("/").data.decode())
r = admin.post("/homepage/image/hero_image",
               data={"image": (io.BytesIO(b"not an image"), "bad.txt")},
               content_type="multipart/form-data", follow_redirects=True)
chk("a non-image is refused", b"not supported" in r.data)
chk("an unknown image key 404s",
    admin.post("/homepage/image/not_a_key",
               data={"image": (io.BytesIO(png), "x.png")},
               content_type="multipart/form-data").status_code == 404)
if os.path.exists(written):
    os.remove(written)
admin.post("/homepage/reset", follow_redirects=True)

print("\n=== 11. the sample report pins follow the sample findings ===")
import re as _re

def _pins(html):
    return _re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="12" fill="(#[0-9A-Fa-f]{6})"', html)

def _rows(html):
    return _re.findall(r'class="inst w-5 h-5 rounded-full', html)

page = anon.get("/").data.decode()
chk("the shipped card draws four pins", len(_pins(page)) == 4, _pins(page))
chk("a pin is drawn for every finding shown",
    len(_pins(page)) == len(_rows(page)), (len(_pins(page)), len(_rows(page))))
chk("the pins sit where the design put them", _pins(page)[0] == ("132.0", "91.0", "#D64550"),
    _pins(page)[0])

form = defaults_for("preview_eyebrow", "preview_image", "preview_footnote", "preview_confirm")
form.update({"section": "preview",
             "preview_rows": "1|#3FA34D|Healthy|Canopy closed evenly.|20|20\n"
                             "2|#E7B416|Monitor|Watch the headland.|80|75"})
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("removing a finding removes its pin", len(_pins(page)) == 2, _pins(page))
chk("the pin moves with the finding", _pins(page)[0] == ("80.0", "52.0", "#3FA34D"), _pins(page)[0])
chk("the list follows too", len(_rows(page)) == 2)
chk("the new wording is live", "Canopy closed evenly." in page)

form["preview_rows"] = ("1|#D64550|Only four parts|no coordinates given\n"
                        "2|#E7B416|Bad numbers|coords are junk|abc|xyz\n\n")
admin.post("/homepage/save", data=form, follow_redirects=True)
page = anon.get("/").data.decode()
chk("a row with no coordinates still renders", anon.get("/").status_code == 200)
chk("and its pin falls back to the centre", '<circle cx="200.0" cy="130.0"' in page)
chk("junk coordinates do not break the page", "Bad numbers" in page)
admin.post("/homepage/reset", follow_redirects=True)
chk("reset brings the four pins back", len(_pins(anon.get("/").data.decode())) == 4)

print("\n=== 12. the editor layout holds together ===")
ed = admin.get("/homepage").data.decode()
import re as _re
_ids = _re.findall(r'id="([^"]+)"', ed)
chk("no element id is repeated", not {i for i in _ids if _ids.count(i) > 1},
    {i for i in _ids if _ids.count(i) > 1})
chk("every section has a nav entry",
    ed.count('data-section="') == ed.count('data-nav="') == len(homepage.SECTIONS),
    (ed.count('data-section="'), ed.count('data-nav="')))
chk("the section list tracks the content", "data-section=" in ed and "data-nav=" in ed)
chk("image panels are scoped to their section", 'id="images"' not in ed)
chk("nothing is left unrendered", "{{" not in ed and "{%" not in ed)
chk("an image upload returns to its own section",
    homepage.section_of("hero_image") == "hero" and homepage.section_of("cta_image") == "cta")
chk("an unknown key has no section", homepage.section_of("nope") == "")

print("\n=== 13. a database with no content table still serves the homepage ===")
with APP.app_context():
    db.session.execute(db.text("DROP TABLE site_content"))
    db.session.commit()
chk("the public homepage still renders", anon.get("/").status_code == 200)
chk("and still shows its shipped copy",
    "The flight lands at noon." in anon.get("/").data.decode())

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
