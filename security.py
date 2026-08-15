"""
Transport and request security for SHAMBA Tracker.

Two jobs, both applied to every request by `install(app)`:

  1. Response headers — the set a scanner checks for, and the reason each one
     is there. They are cheap to send and each closes a real hole: without
     HSTS a farmer's first tap on an http link is interceptable, without a CSP
     a single injected script can read the whole session, without frame-ancestors
     the sign-in form can be framed and clickjacked.

  2. CSRF — a signed, per-session token required on every state-changing
     request. Without it, any page a signed-in agronomist visits can post to
     /findings/<id>/delete in the background and the browser will attach their
     cookie for it.

The content security policy is nonce-based rather than 'unsafe-inline', which
is the only version of a CSP that actually stops injected script. That costs
one attribute on each inline <script> in the templates, and it is why the
Tailwind and Google Fonts CDNs were dropped in favour of the stylesheet and the
font files this project already ships: a policy that has to name a CDN to work
is a policy that trusts that CDN with the whole application.
"""
import hmac
import os
import secrets
from functools import wraps

from flask import abort, current_app, g, request, session

# Methods that change something and therefore need a token.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SESSION_KEY = "_csrf_token"
FORM_FIELD = "csrf_token"
HEADER_NAME = "X-CSRFToken"

# A year, which is what preload lists require and what the scanner recommends.
HSTS_MAX_AGE = 31536000

# Browser features this application never uses. Naming them denies them to any
# script that does end up running here, and denies them to anything embedded.
#
# Only features browsers actually recognise are listed. An unrecognised name is
# not denied — it cannot be, it does not exist — and Chromium logs a warning for
# each one on every page load, which fills the console with noise and hides the
# violations worth reading.
PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()" for feature in (
        "accelerometer", "autoplay", "camera", "display-capture",
        "encrypted-media", "fullscreen", "gamepad", "geolocation", "gyroscope",
        "hid", "idle-detection", "local-fonts", "magnetometer", "microphone",
        "midi", "payment", "picture-in-picture", "publickey-credentials-get",
        "screen-wake-lock", "serial", "usb", "xr-spatial-tracking",
    )
)


# ------------------------------------------------------------------ CSP
def _csp(nonce):
    """
    The policy.

    Every directive is as tight as the application allows:

    * `default-src 'none'` — anything not named below is refused outright,
      so a directive forgotten here fails closed rather than open.
    * `script-src 'nonce-…'` — only scripts carrying this request's nonce run.
      An injected <script> cannot guess it, and because a nonce is present the
      browser ignores 'unsafe-inline' even if something later adds it.
    * `style-src 'self' 'unsafe-inline'` — the report colours every category
      swatch, chart band and table header through a style attribute computed
      from the data. Style attributes cannot carry a nonce, so this one stays;
      it is also the least dangerous of the inline sources, since CSS cannot
      exfiltrate a session on its own.
    * `img-src 'self' data:` — map snapshots are served from this origin, and
      the PDF path inlines the logo as a data URI.
    * `frame-ancestors 'none'` — the modern half of X-Frame-Options.
    * `form-action 'self'` — an injected form cannot post the session anywhere
      but back here.
    """
    return "; ".join([
        "default-src 'none'",
        f"script-src 'self' 'nonce-{nonce}'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
        "manifest-src 'self'",
        "upgrade-insecure-requests",
    ])


def _is_secure():
    """
    Whether this request reached us over TLS.

    Railway terminates TLS at its edge and forwards plain HTTP inward, so
    request.is_secure is only right because ProxyFix has already read
    X-Forwarded-Proto. HSTS is withheld from plain-http requests: a browser
    ignores the header there anyway, and sending it during local development
    would pin localhost to https for a year.
    """
    return request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"


# ------------------------------------------------------------------ CSRF
def csrf_token():
    """This session's token, minted on first use and stable thereafter."""
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def csrf_enabled():
    """
    Off under TESTING so the existing suites can post forms directly.

    WTF_CSRF_ENABLED is honoured as well, because that is the flag people
    reach for out of habit and a test that thinks it has disabled CSRF but
    has not is a confusing afternoon.
    """
    if current_app.config.get("CSRF_ENABLED") is not None:
        return bool(current_app.config["CSRF_ENABLED"])
    if current_app.config.get("WTF_CSRF_ENABLED") is False:
        return False
    return not current_app.config.get("TESTING", False)


def csrf_exempt(view):
    """Mark a view as not needing a token. Used by nothing today, deliberately."""
    view._csrf_exempt = True
    return view


def _validate():
    submitted = (request.form.get(FORM_FIELD)
                 or request.headers.get(HEADER_NAME)
                 or "")
    if not submitted and request.is_json:
        submitted = (request.get_json(silent=True) or {}).get(FORM_FIELD, "")
    expected = session.get(SESSION_KEY, "")
    # compare_digest rather than == so a token cannot be guessed a character at
    # a time by timing the response.
    return bool(expected) and hmac.compare_digest(str(submitted), str(expected))


# ------------------------------------------------------------------ install
def install(app):
    """Wire the headers, the nonce and the CSRF check into the application."""

    # Cookie hardening. These are assigned rather than setdefault-ed: Flask
    # ships the keys already present and set to None/False, so setdefault would
    # find them "already configured" and quietly leave the cookie unprotected —
    # which is exactly the sort of security control that looks applied in the
    # source and is absent in the response.
    #
    # Secure is on unless explicitly disabled, and off under TESTING, since
    # signing in over http://localhost with a Secure cookie hands the browser a
    # cookie it then refuses to send back.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get(
        "SESSION_COOKIE_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = (
        _truthy(os.environ.get("SESSION_COOKIE_SECURE", "1"))
        and not app.config.get("TESTING"))
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = app.config["SESSION_COOKIE_SAMESITE"]
    app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]

    @app.before_request
    def _before():
        # One nonce per request, generated before anything renders.
        g.csp_nonce = secrets.token_urlsafe(18)

        if request.method not in UNSAFE_METHODS or not csrf_enabled():
            return None
        view = app.view_functions.get(request.endpoint)
        if view is not None and getattr(view, "_csrf_exempt", False):
            return None
        if not _validate():
            # 400 rather than 403: the request is malformed, and saying
            # "forbidden" would imply the right session might make it work.
            abort(400, description="Missing or invalid CSRF token. "
                                   "Reload the page and try again.")
        return None

    @app.context_processor
    def _inject():
        return {"csp_nonce": g.get("csp_nonce", ""), "csrf_token": csrf_token}

    @app.after_request
    def _headers(response):
        nonce = g.get("csp_nonce", "")
        response.headers.setdefault("Content-Security-Policy", _csp(nonce))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # Cross-Origin-Embedder-Policy is deliberately not set. It would require
        # every embedded resource to opt in, and the only thing it buys an app
        # with no cross-origin assets and no SharedArrayBuffer is the chance of
        # silently breaking a farmer's map image on an older browser.

        if _is_secure():
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={HSTS_MAX_AGE}; includeSubDomains")

        # A report is a farmer's own field data. Nothing here should sit in a
        # shared cache, and the signed-in screens least of all.
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store" if _private(response) \
                else "public, max-age=3600"
        return response

    return app


def _private(response):
    """Anything HTML, or anything served to a signed-in session."""
    ctype = (response.headers.get("Content-Type") or "").lower()
    return "text/html" in ctype or bool(session)


def _truthy(value):
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")
