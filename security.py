"""
Response security headers and cookie hardening.

A scan of the deployed app in August 2026 came back with none of the six
headers a browser uses to constrain a page, which is what this module adds.
They are set in one place, on every response, so a route added later cannot
quietly ship without them.

What each one is actually doing here:

  Strict-Transport-Security   Stops the first request of a later visit going
                              out over plain HTTP, where the farmer's share
                              link — token and all — would travel in clear.
                              Sent only on a request that already arrived over
                              HTTPS, which is the only context a browser
                              honours it in.
  Content-Security-Policy     Confines scripts, styles, fonts and images to
                              this origin plus the two CDNs the app really
                              loads, and forbids <object>, <base> and posting a
                              form anywhere else. See the note on 'unsafe-*'
                              below — this policy is a real constraint, not a
                              complete one.
  X-Frame-Options             With frame-ancestors, keeps the app out of a
                              third party's iframe, so a click on their page
                              cannot be a click on ours.
  X-Content-Type-Options      Stops a browser second-guessing a Content-Type.
                              An uploaded map is served as an image and must
                              never be sniffed into a document.
  Referrer-Policy             The farmer's report URL contains its own access
                              token. strict-origin-when-cross-origin sends the
                              origin and never the path to another site, so the
                              token does not leak through an outbound link.
  Permissions-Policy          Turns off the device APIs the app never uses, so
                              injected script cannot reach for them either.

Everything here is overridable by environment variable, because a header that
cannot be turned off during an incident tends to get turned off by deleting the
code.
"""
import os
from datetime import timedelta

# Origins the app genuinely loads from. Kept as names so the policy below reads
# as a list of decisions rather than a string.
TAILWIND_CDN = "https://cdn.tailwindcss.com"
GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"
GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"

DEFAULT_HSTS_MAX_AGE = 31536000                    # one year, as recommended

PERMISSIONS_POLICY = ", ".join([
    "accelerometer=()", "autoplay=()", "camera=()", "display-capture=()",
    "encrypted-media=()", "gamepad=()", "geolocation=()", "gyroscope=()",
    "hid=()", "idle-detection=()", "local-fonts=()", "magnetometer=()",
    "microphone=()", "midi=()", "payment=()", "picture-in-picture=()",
    "publickey-credentials-get=()", "screen-wake-lock=()", "serial=()",
    "usb=()", "xr-spatial-tracking=()",
    "fullscreen=(self)",                           # the report is worth a full screen
])


def _flag(name, default):
    return os.environ.get(name, "1" if default else "0").strip().lower() not in (
        "0", "false", "no", "off", "")


def content_security_policy(secure):
    """
    The policy, assembled from the origins the templates actually reference.

    Two allowances are worth naming rather than burying, because they are the
    difference between this policy and a strict one:

      'unsafe-inline'  The interface is server-rendered Jinja with inline
                       <style> and <script> blocks and inline event handlers.
                       Removing it means adding a per-response nonce to every
                       one of them; worth doing, and not worth doing in the
                       same change as the report rewrite.
      'unsafe-eval'    Tailwind's Play CDN compiles utility classes in the
                       browser. Building the stylesheet at deploy time instead
                       removes this line and the CDN origin with it, and is the
                       single highest-value follow-up here.

    Both are on by default and both can be dropped with an environment
    variable once those two jobs are done, so the policy can be tightened
    without another release.
    """
    script = ["'self'", TAILWIND_CDN]
    style = ["'self'", GOOGLE_FONTS_CSS, TAILWIND_CDN]
    if _flag("CSP_ALLOW_INLINE", True):
        script.append("'unsafe-inline'")
        style.append("'unsafe-inline'")
    if _flag("CSP_ALLOW_EVAL", True):
        script.append("'unsafe-eval'")

    directives = [
        ("default-src", ["'self'"]),
        ("base-uri", ["'self'"]),
        ("object-src", ["'none'"]),
        ("frame-src", ["'none'"]),
        ("frame-ancestors", ["'self'"]),
        ("form-action", ["'self'"]),
        ("script-src", script),
        ("style-src", style),
        # data: covers the logo and map inlined into the PDF render; blob: is
        # what the browser hands its own print preview.
        ("img-src", ["'self'", "data:", "blob:"]),
        ("font-src", ["'self'", "data:", GOOGLE_FONTS_FILES]),
        ("connect-src", ["'self'"]),
        ("worker-src", ["'self'", "blob:"]),
        ("manifest-src", ["'self'"]),
        ("media-src", ["'self'"]),
    ]
    policy = "; ".join(f"{name} {' '.join(values)}" for name, values in directives)
    if secure:
        # Only meaningful once the page itself arrived over TLS.
        policy += "; upgrade-insecure-requests"
    return policy


def apply_headers(response, secure):
    """Set every header on one response. Never overwrites one a route already set."""
    def put(name, value):
        if value and name not in response.headers:
            response.headers[name] = value

    if secure and _flag("HSTS_ENABLED", True):
        max_age = os.environ.get("HSTS_MAX_AGE", str(DEFAULT_HSTS_MAX_AGE)).strip()
        hsts = f"max-age={max_age}; includeSubDomains"
        if _flag("HSTS_PRELOAD", False):
            # Preload is a one-way door — the domain is baked into browsers and
            # is slow to undo — so it is opt-in rather than on by default.
            hsts += "; preload"
        put("Strict-Transport-Security", hsts)

    header = ("Content-Security-Policy-Report-Only" if _flag("CSP_REPORT_ONLY", False)
              else "Content-Security-Policy")
    put(header, content_security_policy(secure))

    put("X-Frame-Options", "SAMEORIGIN")
    put("X-Content-Type-Options", "nosniff")
    put("Referrer-Policy", "strict-origin-when-cross-origin")
    put("Permissions-Policy", PERMISSIONS_POLICY)

    # Not graded by the scanners yet, and cheap: they stop another origin
    # holding a handle on our window or embedding our responses.
    put("Cross-Origin-Opener-Policy", "same-origin")
    put("Cross-Origin-Resource-Policy", "same-origin")

    # Nothing this app serves belongs in a shared cache or a search index. The
    # farmer's report is reachable by anyone holding its token, and a token in
    # a search result is a token in the wrong hands.
    put("X-Robots-Tag", "noindex, nofollow")
    return response


def init_app(app):
    """
    Harden the session cookie and hang the headers off every response.

    The Secure flag is on wherever the app is deployed and off on a plain-HTTP
    development machine, because a Secure cookie over http is a cookie the
    browser silently drops — which looks exactly like a broken login. PORT is
    set by Railway and by every other PaaS, and is not set by `python app.py`,
    so it is the signal used here. SESSION_COOKIE_SECURE overrides it either
    way.
    """
    deployed = bool(os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT"))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_flag("SESSION_COOKIE_SECURE", deployed),
        # A signed session that never expires is a stolen session that never
        # expires.
        PERMANENT_SESSION_LIFETIME=timedelta(
            seconds=int(os.environ.get("SESSION_LIFETIME_SECONDS", 60 * 60 * 12))),
    )

    @app.after_request
    def _security_headers(response):
        try:
            from flask import request
            secure = request.is_secure
        except Exception:                          # outside a request context
            secure = False
        return apply_headers(response, secure)

    return app
