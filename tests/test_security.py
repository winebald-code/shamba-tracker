"""
Checks for the security headers.

A scan of the deployed app in August 2026 came back with none of the six
headers a browser uses to constrain a page. These checks pin each one down so a
later release cannot quietly drop it again, and cover the two cases that are
easy to get wrong: HSTS must not be sent to a plain-HTTP request, where the
browser ignores it anyway and it only advertises intent to a network attacker;
and the Secure flag on the session cookie must not be on during local HTTP
development, where the browser silently discards the cookie and the login
simply stops working with no error to explain it.

Run it with:

    python tests/test_security.py
"""
import os, sys, tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(TMP, "test_security.db"),
                  SECRET_KEY="t", ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123")
for key in ("PORT", "RAILWAY_ENVIRONMENT", "SESSION_COOKIE_SECURE",
            "CSP_REPORT_ONLY", "HSTS_ENABLED", "CSP_ALLOW_EVAL", "CSP_ALLOW_INLINE"):
    os.environ.pop(key, None)

import security
import app as appmod

APP = appmod.app
APP.config["TESTING"] = True
c = APP.test_client()

P = F = 0


def chk(label, cond, detail=""):
    global P, F
    print(("  [PASS] " if cond else "  [FAIL] ") + label + ("" if cond else "  -> " + str(detail)))
    P, F = (P + 1, F) if cond else (P, F + 1)


HTTPS = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "tracker.acre-insights.com"}

print("\n=== 1. the six headers the scan asked for ===")
r = c.get("/", headers=HTTPS)
h = r.headers
chk("Strict-Transport-Security", "Strict-Transport-Security" in h, dict(h))
chk("  ...one year, subdomains included",
    "max-age=31536000" in h.get("Strict-Transport-Security", "")
    and "includeSubDomains" in h.get("Strict-Transport-Security", ""),
    h.get("Strict-Transport-Security"))
chk("  ...not preloaded unless asked for",
    "preload" not in h.get("Strict-Transport-Security", ""))
chk("Content-Security-Policy", "Content-Security-Policy" in h)
chk("X-Frame-Options is SAMEORIGIN", h.get("X-Frame-Options") == "SAMEORIGIN", h.get("X-Frame-Options"))
chk("X-Content-Type-Options is nosniff", h.get("X-Content-Type-Options") == "nosniff")
chk("Referrer-Policy protects the farmer's token in an outbound link",
    h.get("Referrer-Policy") == "strict-origin-when-cross-origin", h.get("Referrer-Policy"))
chk("Permissions-Policy", "Permissions-Policy" in h)

print("\n=== 2. what the policy actually allows ===")
csp = h.get("Content-Security-Policy", "")
chk("default-src is this origin", "default-src 'self'" in csp, csp)
chk("no <object> at all", "object-src 'none'" in csp)
chk("no third-party <base>", "base-uri 'self'" in csp)
chk("a form cannot post off-site", "form-action 'self'" in csp)
chk("framing is same-origin, with X-Frame-Options", "frame-ancestors 'self'" in csp)
chk("the Tailwind CDN the app really loads is named", "https://cdn.tailwindcss.com" in csp)
chk("Google Fonts CSS is allowed for styles", "https://fonts.googleapis.com" in csp)
chk("Google Fonts files are allowed for fonts", "https://fonts.gstatic.com" in csp)
chk("the inlined logo and map survive the PDF path", "img-src 'self' data: blob:" in csp)
chk("http subresources are upgraded on a TLS page", "upgrade-insecure-requests" in csp)

print("\n=== 3. HSTS is only meaningful over TLS ===")
plain = c.get("/")
chk("not sent to a plain-HTTP request",
    "Strict-Transport-Security" not in plain.headers, dict(plain.headers))
chk("the other five are still sent",
    all(k in plain.headers for k in ("Content-Security-Policy", "X-Frame-Options",
                                     "X-Content-Type-Options", "Referrer-Policy",
                                     "Permissions-Policy")))
chk("and upgrade-insecure-requests is not claimed off a plain page",
    "upgrade-insecure-requests" not in plain.headers.get("Content-Security-Policy", ""))

print("\n=== 4. every surface, not only the pages that remembered ===")
for path in ("/", "/login", "/signup", "/static/img/acre-logo.png", "/nope-404"):
    resp = c.get(path, headers=HTTPS)
    chk(f"{path} carries the full set",
        all(k in resp.headers for k in ("Content-Security-Policy", "X-Frame-Options",
                                        "X-Content-Type-Options", "Referrer-Policy",
                                        "Permissions-Policy", "Strict-Transport-Security")),
        f"{resp.status_code}: {sorted(resp.headers.keys())}")

print("\n=== 5. nothing here belongs in a search index ===")
chk("X-Robots-Tag keeps the tokenised report link out of results",
    "noindex" in c.get("/", headers=HTTPS).headers.get("X-Robots-Tag", ""))

print("\n=== 6. the session cookie ===")
chk("HttpOnly, so script cannot read it", APP.config["SESSION_COOKIE_HTTPONLY"] is True)
chk("SameSite=Lax", APP.config["SESSION_COOKIE_SAMESITE"] == "Lax")
chk("not Secure on a local HTTP machine, where it would silently vanish",
    APP.config["SESSION_COOKIE_SECURE"] is False, APP.config["SESSION_COOKIE_SECURE"])
chk("sessions expire", APP.config["PERMANENT_SESSION_LIFETIME"].total_seconds() > 0)

print("\n=== 7. login still works with the cookie hardened ===")
r = c.post("/login", data={"email": "a@a.com", "password": "password123"}, follow_redirects=True)
chk("signing in succeeds", r.status_code == 200, r.status_code)
chk("and lands on a dashboard", b"Dashboard" in r.data or b"dashboard" in r.data)

print("\n=== 8. deployed hosts get the Secure flag ===")
os.environ["PORT"] = "8080"
chk("PORT set by the platform turns Secure on",
    security._flag("SESSION_COOKIE_SECURE", bool(os.environ.get("PORT"))) is True)
os.environ["SESSION_COOKIE_SECURE"] = "0"
chk("and an explicit override still wins",
    security._flag("SESSION_COOKIE_SECURE", True) is False)
os.environ.pop("SESSION_COOKIE_SECURE"); os.environ.pop("PORT")

print("\n=== 9. the policy can be tightened without a code change ===")
os.environ["CSP_ALLOW_EVAL"] = "0"
os.environ["CSP_ALLOW_INLINE"] = "0"
strict = security.content_security_policy(True)
chk("'unsafe-eval' comes out", "'unsafe-eval'" not in strict, strict)
chk("'unsafe-inline' comes out", "'unsafe-inline'" not in strict, strict)
os.environ.pop("CSP_ALLOW_EVAL"); os.environ.pop("CSP_ALLOW_INLINE")
chk("and both are back on by default",
    "'unsafe-inline'" in security.content_security_policy(True))

print("\n=== 10. report-only mode, for testing a change safely ===")
os.environ["CSP_REPORT_ONLY"] = "1"
r = c.get("/", headers=HTTPS)
chk("the policy is reported, not enforced",
    "Content-Security-Policy-Report-Only" in r.headers
    and "Content-Security-Policy" not in r.headers, sorted(r.headers.keys()))
os.environ.pop("CSP_REPORT_ONLY")

print(f"\n{'=' * 60}\n  {P} passed, {F} failed\n{'=' * 60}")
sys.exit(1 if F else 0)
