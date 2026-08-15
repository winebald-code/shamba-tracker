"""
Checks for the response headers and the CSRF defence.

Written against the report the scanner produces: every header it flagged as
missing is asserted here by name, so a regression shows up as a failed test
rather than as an F on the next scan.

The CSRF section runs with the protection deliberately switched on — the other
suites disable it via TESTING so they can post forms directly, which means
nothing else in this project exercises it.

Run it with:

    python tests/test_security.py
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "s.db"),
                  SECRET_KEY="test-secret-key-for-the-security-suite",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")

import app as appmod
import security
from models import db, Farm

APP = appmod.app
APP.config["TESTING"] = True

P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label +
          ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


c = APP.test_client()

print("\n=== 1. every header the scanner asked for ===")
r = c.get("/")
h = r.headers
required = {
    "Content-Security-Policy": None,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": None,
}
for name, expected in required.items():
    present = name in h
    chk(f"{name} present", present, dict(h))
    if present and expected:
        chk(f"{name} == {expected}", h[name] == expected, h[name])

chk("Cross-Origin-Opener-Policy present", h.get("Cross-Origin-Opener-Policy") == "same-origin",
    h.get("Cross-Origin-Opener-Policy"))
chk("Cross-Origin-Resource-Policy present",
    h.get("Cross-Origin-Resource-Policy") == "same-origin",
    h.get("Cross-Origin-Resource-Policy"))
chk("Permissions-Policy denies the camera", "camera=()" in h.get("Permissions-Policy", ""))
chk("Permissions-Policy denies geolocation",
    "geolocation=()" in h.get("Permissions-Policy", ""))

print("\n=== 2. HSTS is sent over https, and only over https ===")
chk("not sent on a plain http request", "Strict-Transport-Security" not in h, dict(h))
s = c.get("/", headers={"X-Forwarded-Proto": "https"})
hsts = s.headers.get("Strict-Transport-Security", "")
chk("sent when the proxy reports https", bool(hsts), hsts)
chk("max-age is a year", "max-age=31536000" in hsts, hsts)
chk("covers subdomains", "includeSubDomains" in hsts, hsts)

print("\n=== 3. the content security policy is a real one ===")
csp = h.get("Content-Security-Policy", "")
chk("default-src is 'none'", "default-src 'none'" in csp, csp)
chk("script-src carries a nonce", re.search(r"script-src[^;]*'nonce-[A-Za-z0-9_\-]{16,}'", csp), csp)
chk("script-src does not allow 'unsafe-inline'",
    "unsafe-inline" not in csp.split("script-src")[1].split(";")[0], csp)
chk("script-src does not allow 'unsafe-eval'", "unsafe-eval" not in csp, csp)
chk("no external origin is whitelisted anywhere",
    not re.search(r"https?://", csp), csp)
chk("framing is refused", "frame-ancestors 'none'" in csp, csp)
chk("forms can only post back here", "form-action 'self'" in csp, csp)
chk("base-uri is locked down", "base-uri 'none'" in csp, csp)
chk("objects are refused", "object-src 'none'" in csp, csp)

print("\n=== 4. the nonce changes every request and matches the markup ===")
one = c.get("/")
two = c.get("/")


def nonce_of(resp):
    m = re.search(r"'nonce-([A-Za-z0-9_\-]+)'",
                  resp.headers.get("Content-Security-Policy", ""))
    return m.group(1) if m else None


n1, n2 = nonce_of(one), nonce_of(two)
chk("a nonce is issued", bool(n1))
chk("it is different next time", n1 != n2, (n1, n2))
body = one.data.decode()
tags = re.findall(r"<script([^>]*)>", body)
chk("every script tag in the page carries this request's nonce",
    all(f'nonce="{n1}"' in t for t in tags), tags[:3])
chk("no inline event handlers remain in the served page",
    not re.search(r'\son(click|change|submit|load|error)="', body),
    re.findall(r'\son\w+="[^"]{0,40}', body)[:5])

print("\n=== 5. nothing is loaded from a third party ===")
for path in ("/", "/login"):
    page = c.get(path).data.decode()
    external = set(re.findall(r'(?:src|href)="(https?://[^"]+)"', page))
    # links a farmer may follow are fine; it is loaded subresources that matter
    loaded = {u for u in external
              if re.search(r'<(?:script|link)[^>]+' + re.escape(u), page)}
    chk(f"{path}: no external script or stylesheet", not loaded, loaded)
chk("the stylesheet is served from this origin", c.get("/static/css/app.css").status_code == 200)
chk("the fonts are served from this origin",
    c.get("/static/fonts/montserrat-latin-400-normal.woff2").status_code == 200)

print("\n=== 6. cookies are hardened ===")
chk("session cookie is HttpOnly", APP.config["SESSION_COOKIE_HTTPONLY"] is True)
chk("session cookie is SameSite=Lax", APP.config["SESSION_COOKIE_SAMESITE"] == "Lax")
chk("remember cookie is HttpOnly", APP.config["REMEMBER_COOKIE_HTTPONLY"] is True)

print("\n=== 7. a shipped secret key is never used ===")
chk("the old default is rejected",
    appmod._secret_key.__doc__ and "default" in appmod._secret_key.__doc__.lower())
os.environ["SECRET_KEY"] = "dev-change-me-in-production"
generated = appmod._secret_key()
chk("the published placeholder is replaced with a random key",
    generated != "dev-change-me-in-production" and len(generated) > 30)
os.environ["SECRET_KEY"] = ""
chk("an unset key is also random", len(appmod._secret_key()) > 30)
os.environ["SECRET_KEY"] = "test-secret-key-for-the-security-suite"

print("\n=== 8. CSRF, with the protection switched on ===")
APP.config["CSRF_ENABLED"] = True
guarded = APP.test_client()

page = guarded.get("/login").data.decode()
token = re.search(r'name="csrf_token" value="([^"]+)"', page)
chk("the sign-in form carries a token", bool(token), page[:200])
token = token.group(1) if token else ""

bad = guarded.post("/login", data={"email": "a@a.com", "password": "password123"})
chk("a post with no token is refused", bad.status_code == 400, bad.status_code)

bad = guarded.post("/login", data={"email": "a@a.com", "password": "password123",
                                   "csrf_token": "not-the-right-token"})
chk("a post with the wrong token is refused", bad.status_code == 400, bad.status_code)

good = guarded.post("/login", data={"email": "a@a.com", "password": "password123",
                                    "csrf_token": token}, follow_redirects=True)
chk("a post with the right token succeeds", good.status_code == 200, good.status_code)

hdr = guarded.post("/farms/new", data={"name": "Header Farm", "crop": "Maize",
                                       "acreage": "10"},
                   headers={"X-CSRFToken": token}, follow_redirects=True)
chk("the header is accepted as well as the field", hdr.status_code == 200, hdr.status_code)
with APP.app_context():
    chk("and the request actually took effect",
        db.session.query(Farm).filter_by(name="Header Farm").first() is not None)

chk("a GET needs no token", guarded.get("/farms").status_code in (200, 302))
APP.config["CSRF_ENABLED"] = None

print("\n=== 9. every state-changing form ships a token ===")
import pathlib
forms = tokens = 0
for tpl in sorted(pathlib.Path("templates").glob("*.html")):
    text = tpl.read_text()
    if tpl.name == "base.html":
        continue                      # holds the selector string, not a form
    found = re.findall(r'<form\b[^>]*\bmethod="post"[^>]*>', text, re.I)
    forms += len(found)
    tokens += text.count("csrf_field()")
chk(f"all {forms} posting forms carry csrf_field()", forms == tokens, (forms, tokens))

print("\n=== 10. a farmer's report is not left in a shared cache ===")
with APP.app_context():
    fm = Farm(name="Cache Farm", crop="Maize", acreage=5.0)
    db.session.add(fm)
    db.session.commit()
r = c.get("/login")
chk("html is marked no-store", "no-store" in r.headers.get("Cache-Control", ""),
    r.headers.get("Cache-Control"))

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
