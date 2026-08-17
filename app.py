"""
SHAMBA Tracker — Flight Report Automation
A product of Acre Insights.

Flask + SQLite/Postgres + Tailwind (CDN) + vanilla JS. Ready for Railway (see Dockerfile).
Run locally:  python app.py     (creates the DB and seeds the first admin)
"""
import io
import json
import mimetypes
import os
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, abort, send_file, Response)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import IntegrityError, SQLAlchemyError, OperationalError

from models import (db, User, Farm, Flight, Finding, SiteContent, SummaryEdit,
                    ROLES, ROLE_LABELS,
                    ROLE_BLURB, ROLE_DASHBOARD, STATUS_LABELS, slugify)
import parsing
import integrations
import pdf_gen
import report_data
import schema
import bulk_import
import homepage
import aggregation
import storage as storage_backend

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Where uploads and generated reports actually live. Local disk by default;
# object storage when the S3 variables are set, so files survive a redeploy.
STORE = storage_backend.build_storage(UPLOAD_DIR)

ALLOWED_CSV = {".csv"}
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}

# Who may do what. Everything not listed here is open to any signed-in user.
PERMISSIONS = {
    "manage_users":   {"admin"},
    "manage_homepage": {"admin"},
    "manage_farms":   {"admin", "agronomist", "field_operator"},
    "manage_flights": {"admin", "agronomist", "field_operator"},
    "edit_findings":  {"admin", "agronomist"},
    "generate_report": {"admin", "agronomist"},
    "deliver_report": {"admin", "agronomist", "customer_success"},
    # Customer success takes the farmer's call, so they own the farmer comment
    # even though they cannot edit the finding itself.
    "record_farmer_comment": {"admin", "agronomist", "customer_success"},
}


def create_app():
    app = Flask(__name__)
    # Railway (and every other PaaS) terminates TLS at a proxy and forwards
    # plain HTTP inward. Without this, request.url_root reports the internal
    # http://0.0.0.0:8080 origin, and the report link that goes out to the
    # farmer over WhatsApp points at a host that only exists inside the
    # container. ProxyFix reads the X-Forwarded-* headers so every generated
    # link carries the public https origin instead.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
    db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "shamba.db"))
    if db_url.startswith("postgres://"):          # Railway hands out the legacy prefix
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # SQLite: wait on the lock instead of failing immediately when workers overlap.
    if db_url.startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 15}}
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # 25 MB uploads
    app.config["UPLOAD_DIR"] = UPLOAD_DIR

    db.init_app(app)

    login = LoginManager(app)
    login.login_view = "login"
    login.login_message_category = "warn"

    @login.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    # make colour code, categories and permissions available to every template
    @app.context_processor
    def inject_globals():
        return {
            "COLOUR_CODE": parsing.COLOUR_CODE,
            "CATEGORIES": parsing.CATEGORIES,
            "CATEGORY_COLOURS": aggregation.CATEGORY_COLOURS,
            "ROLE_LABELS": ROLE_LABELS,
            "ROLE_BLURB": ROLE_BLURB,
            "STATUS_LABELS": STATUS_LABELS,
            "ROLES": ROLES,
            "can": can,
            "pending_count": _pending_count(),
            "now": datetime.utcnow(),
            "PDF_AVAILABLE": pdf_gen.PDF_AVAILABLE,
            "site": site_content(),
            "split_lines": homepage.split_lines,
            "rich": homepage.rich,
            "asset_url": asset_url,
        }

    if pdf_gen.PDF_AVAILABLE:
        print("[pdf] WeasyPrint ready — reports download as PDF files")
    else:
        print("[pdf] WeasyPrint unavailable — the Download button will open the "
              "browser's print dialog instead. Build with the included Dockerfile "
              "to get server-generated PDFs.")

    register_security_headers(app)
    register_routes(app)

    with app.app_context():
        init_db()

    return app


# ------------------------------------------------------------------ security
# Content-Security-Policy sources. Tailwind is loaded from its CDN and compiles
# in the browser, and Google Fonts serves the application's typeface, so both
# origins have to be allowed.
CSP_DIRECTIVES = (
    "default-src 'self'",
    # 'unsafe-inline' and 'unsafe-eval' are needed by the Tailwind browser build,
    # which generates styles at runtime. Removing them means moving to a compiled
    # stylesheet, which is a build-step change rather than a header change.
    "script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'",
    "font-src 'self' https://fonts.gstatic.com data:",
    # data: covers the base64 images inlined into a rendered report.
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "upgrade-insecure-requests",
)

SECURITY_HEADERS = {
    # Tells the browser to reach this site over HTTPS only. Two years, covering
    # subdomains, and preload-eligible.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "Content-Security-Policy": "; ".join(CSP_DIRECTIVES),
    # frame-ancestors above covers modern browsers; this covers the rest.
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # A report link is a capability. Sending the full URL in a Referer header
    # to another origin would leak a farmer's share token.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": ("accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                           "magnetometer=(), microphone=(), payment=(), usb=(), "
                           "interest-cohort=()"),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def register_security_headers(app):
    """
    Set the response headers a browser uses to defend the application.

    Every one of these was missing, which is what a security scan grades on.
    They are applied here rather than at the proxy so they travel with the
    application to whatever host it is deployed on.
    """
    @app.after_request
    def _security_headers(response):
        for header, value in SECURITY_HEADERS.items():
            # HSTS over plain HTTP is meaningless and browsers ignore it, but
            # sending it locally would pin developers to https://localhost.
            if header == "Strict-Transport-Security" and not request.is_secure:
                forwarded = request.headers.get("X-Forwarded-Proto", "")
                if forwarded.split(",")[0].strip() != "https":
                    continue
            response.headers.setdefault(header, value)
        return response


def _pending_count():
    """Badge count for the admin nav. Never raises, even before the table exists."""
    try:
        if current_user.is_authenticated and current_user.role == "admin":
            return User.query.filter_by(status="pending").count()
    except Exception:
        pass
    return 0


def init_db():
    """
    Create tables, reconcile any newly added columns, and seed the first admin —
    safely, even when several gunicorn workers start at once against the same
    database. The worker that wins the first CREATE completes them all; the
    others hit "already exists" and simply move on.
    """
    try:
        db.create_all()
    except OperationalError:
        db.session.rollback()   # another worker created the tables first
    schema.ensure_schema(db)    # add columns this release introduced
    seed_admin()


def seed_admin():
    """
    Create the first admin if there are no users yet.

    Safe under multiple gunicorn workers booting at once: if another worker
    inserts the same admin a moment earlier, the duplicate insert raises
    IntegrityError, which we roll back and ignore instead of crashing the worker.
    """
    email = os.environ.get("ADMIN_EMAIL", "cathy@acre-insights.com")
    try:
        if User.query.first() is not None or User.query.filter_by(email=email).first() is not None:
            return
    except OperationalError:
        db.session.rollback()
        return
    pw = os.environ.get("ADMIN_PASSWORD", "AcreInsights2026")
    first = User(name=os.environ.get("ADMIN_NAME", "Cathy"), email=email, role="admin",
                 active=True, status="approved", requested_role="admin",
                 job_title="Customer Success Lead", approved_at=datetime.utcnow())
    first.set_password(pw)
    db.session.add(first)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()   # another worker already seeded — that's fine
        return
    print("=" * 64)
    print(" Seeded first admin user")
    print(f"   email:    {email}")
    print(f"   password: {pw}")
    print("   (set ADMIN_EMAIL / ADMIN_PASSWORD env vars to change these)")
    print("=" * 64)


# ------------------------------------------------------------------ access
def can(action, user=None):
    """True when the user may perform `action`. Used in routes and templates."""
    user = user or current_user
    if not getattr(user, "is_authenticated", False):
        return False
    allowed = PERMISSIONS.get(action)
    return True if allowed is None else user.role in allowed


def requires(action):
    """Route guard for a named permission."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                return redirect(url_for("login", next=request.path))
            if not can(action):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated:
            return redirect(url_for("login", next=request.path))
        if current_user.role != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ------------------------------------------------------------------ helpers
def _save_upload(file_storage, prefix, allowed):
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in allowed:
        return None, f"Unsupported file type: {ext or 'unknown'}"
    name = secure_filename(f"{prefix}_{int(datetime.utcnow().timestamp())}{ext}")
    try:
        file_storage.stream.seek(0)
        STORE.save_fileobj(file_storage.stream, name)
    except Exception as exc:
        return None, f"Could not store the file: {exc}"
    return name, None


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def report_context(flight):
    """Everything the report template needs, incl. the season-to-date trend."""
    # Any route that renders a report may also be about to hand its link out,
    # so this is the right place to make sure the link exists at all.
    if flight.ensure_share_token():
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Every flight of this farm and season, so the report can show the season
    # so far rather than only the flight before this one.
    season_flights = (Flight.query
                      .filter_by(farm_id=flight.farm_id, season=flight.season)
                      .order_by(Flight.flight_number)
                      .all())

    prev = next((f for f in season_flights
                 if f.flight_number == (flight.flight_number or 0) - 1), None)
    resolved = still_open = prev_total = None
    if prev:
        prev_total = len(prev.findings)
        resolved = sum(1 for f in flight.findings if f.resolved)
        still_open = len(flight.findings) - resolved

    analysis = report_data.analyse(flight, prev)

    # The findings are grouped into the few patterns actually present in them
    # rather than listed one by one. The map numbers go in with them so the
    # summary, the map key and the detail tables all call a zone by one number.
    # Zone numbers follow the order the areas were annotated in, which is the
    # order DroneDeploy used when it numbered them on the image the report
    # carries. Numbering them any other way puts the key and the map at odds:
    # a farmer reading "zone 5" would be looking at a different area from the
    # one the report describes.
    zone_numbers = {f.id: i + 1 for i, f in enumerate(
        sorted(flight.findings, key=lambda f: (f.sort_order or 0, f.id or 0)))}
    agg = aggregation.aggregate(flight.findings, zone_numbers)
    # Every flight of this farm and season, so the report can show how the
    # season has moved rather than only what this flight found.
    season_overview = aggregation.season_overview(season_flights, flight.id,
                                                  flight.flight_number)

    # An agronomist's own wording for a pattern replaces the assembled sentence.
    # Applied here rather than inside the aggregation so that layer stays a pure
    # function of the findings and can be reasoned about on its own.
    if agg:
        edits = {e.category: e for e in flight.summary_edits}
        for group in agg["groups"]:
            edit = edits.get(group["name"])
            group["observation_edited"] = bool(edit and (edit.observation or "").strip())
            group["suggestion_edited"] = bool(edit and (edit.suggestion or "").strip())
            if group["observation_edited"]:
                group["observation"] = edit.observation.strip()
            if group["suggestion_edited"]:
                group["suggestion"] = edit.suggestion.strip()
    points = report_data.season_trend(flight, season_flights)
    season = report_data.season_summary(points, analysis["score"]) if points else None

    return {
        "flight": flight,
        "farm": flight.farm,
        "findings": flight.findings,
        "category_counts": flight.category_counts(),
        "meaning_counts": flight.meaning_counts(),
        "prev": prev,
        "prev_total": prev_total,
        "resolved": resolved,
        "still_open": still_open,
        "a": analysis,
        "agg": agg,
        "summary_line": aggregation.summary_sentence(agg, flight, flight.farm),
        "season_overview": season_overview,
        "season": season,
        "season_flights": season_flights,
        "next_flight_no": flight.flight_number + 1,
    }


# ------------------------------------------------------------------ routes
def register_routes(app):

    # ---- landing ----
    @app.route("/")
    def home():
        # A signed-in user lands on their dashboard rather than the sales page.
        # `?preview=1` opts out of that, so an admin editing the homepage can
        # open it in a new tab and see what a visitor sees without signing out.
        if current_user.is_authenticated and not request.args.get("preview"):
            return redirect(url_for("dashboard"))
        return render_template("home.html")

    # ---- auth ----
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        first_ever = User.query.count() == 0
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            job_title = request.form.get("job_title", "").strip()
            phone = request.form.get("phone", "").strip()
            wanted = request.form.get("requested_role", "agronomist")
            if wanted not in ROLES or wanted == "admin":
                wanted = "agronomist"
            if not (name and email and pw):
                flash("Fill in your name, email and a password to request access.", "warn")
            elif len(pw) < 6:
                flash("Use a password of at least 6 characters.", "warn")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists. Try signing in.", "warn")
            else:
                u = User(name=name, email=email, phone=phone, job_title=job_title,
                         requested_role=wanted)
                if first_ever:
                    # Bootstrapping: the very first account owns the workspace,
                    # so there is nobody left to approve it.
                    u.role, u.status, u.active = "admin", "approved", True
                    u.approved_at = datetime.utcnow()
                    u.set_password(pw)
                    db.session.add(u)
                    db.session.commit()
                    login_user(u)
                    flash(f"Welcome to SHAMBA Tracker, {name.split()[0]}. "
                          "You are the workspace admin, so you approve everyone who joins next.", "ok")
                    return redirect(url_for("dashboard"))

                u.role = wanted          # provisional; an admin confirms it on approval
                u.status = "pending"
                u.active = True
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
                _notify_admins_of_signup(u)
                return redirect(url_for("pending", email=email))
        return render_template("signup.html", first_ever=first_ever)

    @app.route("/pending")
    def pending():
        """Shown after signing up, and to anyone who signs in before approval."""
        email = request.args.get("email", "")
        u = User.query.filter_by(email=email.lower()).first() if email else None
        return render_template("pending.html", pending_user=u, email=email)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            u = User.query.filter_by(email=email).first()
            if not (u and u.check_password(pw)):
                flash("Those details didn't match any account.", "warn")
            elif u.status == "pending":
                return redirect(url_for("pending", email=u.email))
            elif u.status == "rejected":
                flash("That request for access was declined. Contact your workspace admin "
                      "if you think this is wrong.", "warn")
            elif not u.active:
                flash("That account has been deactivated. Contact your workspace admin.", "warn")
            else:
                login_user(u)
                u.last_login_at = datetime.utcnow()
                db.session.commit()
                nxt = request.args.get("next")
                if nxt and nxt.startswith("/"):        # never redirect off-site
                    return redirect(nxt)
                return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("home"))

    # ---- dashboards, one per role ----
    @app.route("/dashboard")
    @login_required
    def dashboard():
        return redirect(url_for(ROLE_DASHBOARD.get(current_user.role, "dashboard_agronomist")))

    @app.route("/dashboard/admin")
    @login_required
    @admin_required
    def dashboard_admin():
        flights = Flight.query.all()
        findings = Finding.query.all()
        cat_counts = {}
        for f in findings:
            cat_counts[f.category] = cat_counts.get(f.category, 0) + 1
        team = User.query.order_by(User.created_at.desc()).all()
        stats = {
            "farms": Farm.query.count(),
            "flights": len(flights),
            "issues": len(findings),
            "reports": sum(1 for fl in flights if fl.report_generated),
            "sent": sum(1 for fl in flights if fl.is_delivered),
            "acknowledged": sum(1 for fl in flights if fl.acknowledged),
        }
        role_counts = {}
        for u in team:
            if u.status == "approved":
                role_counts[u.role] = role_counts.get(u.role, 0) + 1
        return render_template(
            "dashboard_admin.html", stats=stats, cat_counts=cat_counts,
            recent=Flight.query.order_by(Flight.created_at.desc()).limit(6).all(),
            awaiting=[u for u in team if u.status == "pending"],
            team=[u for u in team if u.status == "approved"],
            role_counts=role_counts,
            providers=integrations.provider_status())

    @app.route("/dashboard/agronomist")
    @login_required
    def dashboard_agronomist():
        flights = Flight.query.order_by(Flight.created_at.desc()).all()
        needs_work = [f for f in flights if f.findings and f.incomplete_count > 0]
        no_data = [f for f in flights if not f.findings]
        ready = [f for f in flights if f.can_generate and not f.report_generated]
        generated = [f for f in flights if f.report_generated]
        open_findings = Finding.query.filter_by(resolved=False).count()
        stats = {
            "needs_work": len(needs_work),
            "no_data": len(no_data),
            "ready": len(ready),
            "generated": len(generated),
            "open_findings": open_findings,
            "farms": Farm.query.count(),
        }
        return render_template("dashboard_agronomist.html", stats=stats,
                               needs_work=needs_work[:8], ready=ready[:8],
                               no_data=no_data[:6], recent=flights[:6])

    @app.route("/dashboard/customer-success")
    @login_required
    def dashboard_cs():
        flights = Flight.query.order_by(Flight.created_at.desc()).all()
        to_review = [f for f in flights if f.status == "Ready for Review"]
        to_send = [f for f in flights if f.report_generated and not f.is_delivered]
        awaiting_ack = [f for f in flights if f.is_delivered and not f.acknowledged]
        acknowledged = [f for f in flights if f.acknowledged]
        missing_contact = [f for f in flights
                           if f.report_generated and not (f.farm.farmer_email or f.farm.farmer_phone)]
        stats = {
            "to_review": len(to_review),
            "to_send": len(to_send),
            "awaiting_ack": len(awaiting_ack),
            "acknowledged": len(acknowledged),
            "missing_contact": len(missing_contact),
            "farms": Farm.query.count(),
        }
        return render_template("dashboard_cs.html", stats=stats, to_review=to_review[:8],
                               to_send=to_send[:8], awaiting_ack=awaiting_ack[:8],
                               missing_contact=missing_contact[:6],
                               providers=integrations.provider_status())

    @app.route("/dashboard/field")
    @login_required
    def dashboard_operator():
        flights = Flight.query.order_by(Flight.created_at.desc()).all()
        no_map = [f for f in flights if not f.map_image]
        no_csv = [f for f in flights if not f.csv_filename]
        recent_30 = [f for f in flights
                     if f.flight_date and f.flight_date >= (date.today() - timedelta(days=30))]
        stats = {
            "farms": Farm.query.count(),
            "flights": len(flights),
            "no_map": len(no_map),
            "no_csv": len(no_csv),
            "last_30": len(recent_30),
            "acres": round(sum((f.acreage or f.farm.acreage or 0) for f in flights), 1),
        }
        return render_template("dashboard_operator.html", stats=stats,
                               no_map=no_map[:8], no_csv=no_csv[:8], recent=flights[:8],
                               farms=Farm.query.order_by(Farm.name).all())

    # ---- profile (every user, full CRUD on their own record) ----
    @app.route("/profile")
    @login_required
    def profile():
        my_flights = []
        if current_user.role in ("agronomist", "field_operator", "admin"):
            my_flights = Flight.query.order_by(Flight.created_at.desc()).limit(5).all()
        return render_template("profile.html", u=current_user, my_flights=my_flights)

    @app.route("/profile/edit", methods=["POST"])
    @login_required
    def profile_edit():
        u = current_user
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        if not name:
            flash("Your name can't be empty.", "warn")
            return redirect(url_for("profile"))
        if email and email != u.email:
            if User.query.filter(User.email == email, User.id != u.id).first():
                flash("Another account already uses that email.", "warn")
                return redirect(url_for("profile"))
            u.email = email
        u.name = name
        u.phone = request.form.get("phone", "").strip()
        u.job_title = request.form.get("job_title", "").strip()
        u.location = request.form.get("location", "").strip()
        u.bio = request.form.get("bio", "").strip()
        db.session.commit()
        flash("Profile updated.", "ok")
        return redirect(url_for("profile"))

    @app.route("/profile/password", methods=["POST"])
    @login_required
    def profile_password():
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("That current password isn't right.", "warn")
        elif len(new) < 6:
            flash("Use a new password of at least 6 characters.", "warn")
        elif new != confirm:
            flash("The two new passwords don't match.", "warn")
        else:
            current_user.set_password(new)
            db.session.commit()
            flash("Password changed.", "ok")
        return redirect(url_for("profile"))

    @app.route("/profile/delete", methods=["POST"])
    @login_required
    def profile_delete():
        u = current_user
        if request.form.get("confirm", "").strip().lower() != u.email.lower():
            flash("Type your email address exactly to confirm deletion.", "warn")
            return redirect(url_for("profile"))
        if u.role == "admin" and User.query.filter_by(role="admin", status="approved").count() <= 1:
            flash("You are the only admin. Promote somebody else before deleting your account.", "warn")
            return redirect(url_for("profile"))
        User.query.filter_by(approved_by_id=u.id).update({"approved_by_id": None})
        db.session.delete(u)
        db.session.commit()
        logout_user()
        flash("Your account has been deleted.", "ok")
        return redirect(url_for("home"))

    # ---- farms ----
    @app.route("/farms")
    @login_required
    def farms():
        return render_template("farms.html", farms=Farm.query.order_by(Farm.name).all())

    @app.route("/farms/new", methods=["POST"])
    @requires("manage_farms")
    def farm_new():
        f = Farm(
            name=request.form.get("name", "").strip(),
            crop=request.form.get("crop", "").strip(),
            acreage=_float(request.form.get("acreage")),
            location=request.form.get("location", "").strip(),
            farmer_name=request.form.get("farmer_name", "").strip(),
            farmer_email=request.form.get("farmer_email", "").strip(),
            farmer_phone=request.form.get("farmer_phone", "").strip(),
            dronedeploy_project_url=request.form.get("dronedeploy_project_url", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        if not f.name:
            flash("A farm needs a name.", "warn")
            return redirect(url_for("farms"))
        db.session.add(f)
        db.session.commit()
        flash(f"Added {f.name}.", "ok")
        return redirect(url_for("farm_detail", farm_id=f.id))

    @app.route("/farms/<int:farm_id>")
    @login_required
    def farm_detail(farm_id):
        farm = db.get_or_404(Farm, farm_id)
        return render_template("farm_detail.html", farm=farm)

    @app.route("/farms/<int:farm_id>/edit", methods=["POST"])
    @requires("manage_farms")
    def farm_edit(farm_id):
        farm = db.get_or_404(Farm, farm_id)
        farm.name = request.form.get("name", farm.name).strip()
        farm.crop = request.form.get("crop", "").strip()
        farm.acreage = _float(request.form.get("acreage"))
        farm.location = request.form.get("location", "").strip()
        farm.farmer_name = request.form.get("farmer_name", "").strip()
        farm.farmer_email = request.form.get("farmer_email", "").strip()
        farm.farmer_phone = request.form.get("farmer_phone", "").strip()
        previous_url = (farm.dronedeploy_project_url or "").strip()
        farm.dronedeploy_project_url = request.form.get("dronedeploy_project_url", "").strip()
        if previous_url and previous_url != farm.dronedeploy_project_url:
            for fl in farm.flights:
                if (fl.dronedeploy_project_url or "").strip() == previous_url:
                    fl.dronedeploy_project_url = ""
        farm.notes = request.form.get("notes", "").strip()
        db.session.commit()
        flash("Farm updated.", "ok")
        return redirect(url_for("farm_detail", farm_id=farm.id))

    @app.route("/farms/<int:farm_id>/delete", methods=["POST"])
    @requires("manage_farms")
    def farm_delete(farm_id):
        farm = db.get_or_404(Farm, farm_id)
        db.session.delete(farm)
        db.session.commit()
        flash("Farm removed.", "ok")
        return redirect(url_for("farms"))

    # ---- homepage content ----
    @app.route("/homepage")
    @requires("manage_homepage")
    def homepage_edit():
        rows = {r.key: r for r in SiteContent.query.all()}
        return render_template("homepage_edit.html",
                               sections=homepage.SECTIONS,
                               values=site_content(),
                               defaults=homepage.DEFAULTS,
                               rows=rows)

    @app.route("/homepage/save", methods=["POST"])
    @requires("manage_homepage")
    def homepage_save():
        """
        Save the edited values.

        A field is stored only when it differs from the default, and a row is
        deleted when it returns to the default, so the table holds the changes
        rather than a full copy of the page. That keeps a later release free to
        reword an untouched default and have the change actually show.
        """
        changed = 0
        rows = {r.key: r for r in SiteContent.query.all()}
        for key, default in homepage.DEFAULTS.items():
            if key not in request.form:
                continue                       # a section posted on its own
            value = request.form.get(key, "").replace("\r\n", "\n").strip()
            row = rows.get(key)
            if value == default.strip():
                if row:
                    db.session.delete(row)
                    changed += 1
                continue
            if row:
                if row.value != value:
                    row.value = value
                    row.updated_by_id = current_user.id
                    changed += 1
            else:
                db.session.add(SiteContent(key=key, value=value,
                                           updated_by_id=current_user.id))
                changed += 1
        db.session.commit()
        flash(f"Homepage updated — {changed} field(s) changed." if changed
              else "Nothing changed.", "ok" if changed else "warn")
        return redirect(url_for("homepage_edit") + "#" + request.form.get("section", ""))

    @app.route("/homepage/image/<key>", methods=["POST"])
    @requires("manage_homepage")
    def homepage_image(key):
        """Replace one of the homepage images with an upload."""
        if key not in homepage.IMAGE_KEYS:
            abort(404)
        file = request.files.get("image")
        if not file or not file.filename:
            flash("Choose an image to upload.", "warn")
            return redirect(url_for("homepage_edit"))
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_IMG:
            flash("That image type is not supported. Use PNG, JPG or WEBP.", "warn")
            return redirect(url_for("homepage_edit"))

        # Stored under a per-key name, so an upload replaces the previous one
        # instead of leaving orphans behind on every edit. It goes to the same
        # store as map snapshots rather than to static/, because static/ is part
        # of the container image and is recreated on every deploy.
        name = f"home-{key.replace('_', '-')}{ext}"
        try:
            file.stream.seek(0)
            STORE.save_fileobj(file.stream, name)
        except Exception as exc:
            flash(f"Could not store that image: {exc}", "warn")
            return redirect(url_for("homepage_edit"))
        rel = f"uploads/{name}"

        row = SiteContent.query.filter_by(key=key).first()
        if row:
            row.value = rel
            row.updated_by_id = current_user.id
        else:
            db.session.add(SiteContent(key=key, value=rel, updated_by_id=current_user.id))
        db.session.commit()
        flash("Image replaced.", "ok")
        return redirect(url_for("homepage_edit") + "#" + homepage.section_of(key))

    @app.route("/homepage/reset", methods=["POST"])
    @requires("manage_homepage")
    def homepage_reset():
        """Drop every saved value so the homepage returns to its shipped text."""
        n = SiteContent.query.delete()
        db.session.commit()
        flash(f"Homepage reset to defaults — {n} saved field(s) cleared.", "ok")
        return redirect(url_for("homepage_edit"))

    # ---- bulk import of farms and flights ----
    @app.route("/import")
    @requires("manage_farms")
    def import_home():
        return render_template("import.html", kind=request.args.get("kind", "farms"),
                               plans=None, summary=None, headers=None)

    @app.route("/import/template/<kind>.csv")
    @requires("manage_farms")
    def import_template(kind):
        """The starter sheet, with the exact headings the importer reads."""
        if kind not in ("farms", "flights"):
            abort(404)
        body = bulk_import.template_csv(kind)
        name = f"SHAMBA Tracker {kind} import template.csv"
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition": _content_disposition(name)})

    @app.route("/import/<kind>/preview", methods=["POST"])
    @requires("manage_farms")
    def import_preview(kind):
        """
        Read the upload and show what it would do. Nothing is written here —
        a bulk change to farms or flights is worth seeing before it lands.
        """
        if kind not in ("farms", "flights"):
            abort(404)
        file = request.files.get("sheet")
        if not file or not file.filename:
            flash("Choose a CSV or Excel file to import.", "warn")
            return redirect(url_for("import_home", kind=kind))

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in bulk_import.ALLOWED_EXTS:
            flash("That file type can't be read. Upload a .csv, .xlsx or .xlsm file.", "warn")
            return redirect(url_for("import_home", kind=kind))

        try:
            headers, rows = bulk_import.read_rows(file.read(), file.filename)
        except Exception as exc:
            flash(f"Could not read that file: {exc}", "warn")
            return redirect(url_for("import_home", kind=kind))

        if not rows:
            flash("That file has a heading row but no data rows under it.", "warn")
            return redirect(url_for("import_home", kind=kind))
        if len(rows) > bulk_import.MAX_ROWS:
            flash(f"That file has {len(rows)} rows. Split it into files of "
                  f"{bulk_import.MAX_ROWS} rows or fewer.", "warn")
            return redirect(url_for("import_home", kind=kind))

        plans, problem = _plan_import(kind, headers, rows)
        if problem:
            flash(problem, "warn")
            return redirect(url_for("import_home", kind=kind))

        return render_template("import.html", kind=kind, plans=plans,
                               summary=bulk_import.summarise(plans),
                               headers=headers, filename=file.filename,
                               payload=json.dumps({"kind": kind, "headers": headers,
                                                   "rows": rows}))

    @app.route("/import/<kind>/apply", methods=["POST"])
    @requires("manage_farms")
    def import_apply(kind):
        """Write the rows the preview showed. Rows in error are skipped."""
        if kind not in ("farms", "flights"):
            abort(404)
        try:
            payload = json.loads(request.form.get("payload", ""))
            headers, rows = payload["headers"], payload["rows"]
            if payload.get("kind") != kind:
                raise ValueError("kind mismatch")
        except Exception:
            flash("That import expired. Upload the file again.", "warn")
            return redirect(url_for("import_home", kind=kind))

        # Re-planned against the database as it is now, not as it was when the
        # preview was drawn — another person may have added a farm in between.
        plans, problem = _plan_import(kind, headers, rows)
        if problem:
            flash(problem, "warn")
            return redirect(url_for("import_home", kind=kind))

        created = updated = 0
        for plan in plans:
            if plan["action"] not in ("create", "update"):
                continue
            if kind == "farms":
                created, updated = _apply_farm(plan, created, updated)
            else:
                created, updated = _apply_flight(plan, created, updated)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The import clashed with a record that changed while you were "
                  "reviewing it. Upload the file again.", "warn")
            return redirect(url_for("import_home", kind=kind))

        skipped = sum(1 for p in plans if p["action"] == "error")
        noun = "farm" if kind == "farms" else "flight"
        parts = []
        if created:
            parts.append(f"{created} {noun}{'' if created == 1 else 's'} added")
        if updated:
            parts.append(f"{updated} updated")
        if skipped:
            parts.append(f"{skipped} skipped")
        flash("Import finished — " + (", ".join(parts) if parts else "nothing to do") + ".",
              "ok" if (created or updated) else "warn")
        return redirect(url_for("farms") if kind == "farms" else url_for("import_home", kind=kind))

    # ---- flights ----
    @app.route("/farms/<int:farm_id>/flights/new", methods=["POST"])
    @requires("manage_flights")
    def flight_new(farm_id):
        farm = db.get_or_404(Farm, farm_id)
        try:
            fnum = int(request.form.get("flight_number", "1"))
            planned = int(request.form.get("flights_planned", "1"))
        except ValueError:
            flash("Flight number and flights planned must be whole numbers.", "warn")
            return redirect(url_for("farm_detail", farm_id=farm.id))
        fl = Flight(
            farm_id=farm.id,
            season=request.form.get("season", "").strip(),
            flight_number=fnum,
            flights_planned=planned,
            crop=request.form.get("crop", "").strip() or farm.crop,
            acreage=_float(request.form.get("acreage")) or farm.acreage,
            flight_date=_parse_date(request.form.get("flight_date")),
            dronedeploy_project_url=request.form.get("dronedeploy_project_url", "").strip(),
            status="Draft",
        )
        if not fl.season:
            flash("A flight needs a season (e.g. 2026LR).", "warn")
            return redirect(url_for("farm_detail", farm_id=farm.id))
        db.session.add(fl)
        db.session.commit()
        flash(f"Flight {fl.flight_number} created for {farm.name}.", "ok")
        return redirect(url_for("flight_detail", flight_id=fl.id))

    @app.route("/flights/<int:flight_id>")
    @login_required
    def flight_detail(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        return render_template("flight_detail.html", flight=flight,
                               ctx=report_context(flight))

    @app.route("/flights/<int:flight_id>/upload_csv", methods=["POST"])
    @requires("manage_flights")
    def flight_upload_csv(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        file = request.files.get("csv")
        if not file or not file.filename:
            flash("Choose a DroneDeploy CSV export to upload.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_CSV:
            flash("That doesn't look like a .csv export.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        raw = file.read()
        try:
            parsed = parsing.parse_csv(raw)
        except Exception as e:
            flash(f"Could not read that CSV: {e}", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))

        # replace existing findings for an idempotent re-import
        Finding.query.filter_by(flight_id=flight.id).delete()
        for p in parsed:
            db.session.add(Finding(
                flight_id=flight.id,
                annotation_id=p["annotation_id"], label_hex=p["label_hex"],
                colour_swatch=p["colour_swatch"], colour_meaning=p["colour_meaning"],
                category=p["category"], observation=p["observation"],
                likely_cause=p["likely_cause"], recommendation=p["recommendation"],
                area_text=p["area_text"], area_acres=p["area_acres"],
                measurement_type=p["measurement_type"], geometry_type=p["geometry_type"],
                annotation_link=p["annotation_link"], sort_order=p["sort_order"],
            ))
        flight.csv_filename = secure_filename(file.filename)
        flight.report_generated = False
        db.session.commit()
        flash(f"Imported {len(parsed)} annotation(s). Review and complete each one below.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/upload_map", methods=["POST"])
    @requires("manage_flights")
    def flight_upload_map(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        file = request.files.get("map")
        if not file or not file.filename:
            flash("Choose a map image to upload.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        name, err = _save_upload(file, f"map_flight{flight.id}", ALLOWED_IMG)
        if err:
            flash(err, "warn")
        else:
            flight.map_image = name
            flight.report_generated = False
            db.session.commit()
            flash("Map snapshot uploaded.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/status", methods=["POST"])
    @login_required
    def flight_status(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        new = request.form.get("status", "").strip()
        if new in ("Draft", "Ready for Review", "Approved", "Sent"):
            flight.status = new
            db.session.commit()
            # Ready-for-Review notifies Customer Success (silent unless keys are set)
            if new == "Ready for Review":
                for reviewer in User.query.filter(
                        User.role.in_(["customer_success", "admin"]),
                        User.status == "approved").all():
                    subject = (f"{flight.farm.name} ({flight.season}) — Flight "
                               f"{flight.flight_number}/{flight.flights_planned} ready for review")
                    body = (f"{flight.farm.name}, {flight.season}, Flight {flight.flight_number} of "
                            f"{flight.flights_planned} is annotated and ready for review.\n\n"
                            f"Open it: {url_for('flight_detail', flight_id=flight.id, _external=True)}")
                    integrations.send_email(reviewer.email, subject, body)
                flash("Marked ready — Customer Success has been notified.", "ok")
            else:
                flash(f"Status set to {new}.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/delete", methods=["POST"])
    @requires("manage_flights")
    def flight_delete(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        farm_id = flight.farm_id
        db.session.delete(flight)
        db.session.commit()
        flash("Flight removed.", "ok")
        return redirect(url_for("farm_detail", farm_id=farm_id))

    @app.route("/flights/<int:flight_id>/note", methods=["POST"])
    @requires("edit_findings")
    def flight_note(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        flight.agronomist_note = request.form.get("note", "").strip()
        db.session.commit()
        flash("Note saved.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    # ---- findings CRUD ----
    @app.route("/flights/<int:flight_id>/findings/add", methods=["POST"])
    @requires("edit_findings")
    def finding_add(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        order = (max([f.sort_order for f in flight.findings], default=-1)) + 1
        f = Finding(flight_id=flight.id, category="Needs Investigation",
                    colour_meaning="Pending review", colour_swatch="#6C6C6C",
                    sort_order=order, annotation_id=f"manual-{order+1}")
        flight.report_generated = False
        db.session.add(f)
        db.session.commit()
        flash("Finding added — fill it in below.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id) + "#f" + str(f.id))

    @app.route("/findings/<int:finding_id>/update", methods=["POST"])
    @requires("edit_findings")
    def finding_update(finding_id):
        f = db.get_or_404(Finding, finding_id)
        data = request.get_json(force=True)
        for field in ("category", "observation", "likely_cause", "recommendation"):
            if field in data:
                setattr(f, field, (data[field] or "").strip())
        if "resolved" in data:
            f.resolved = bool(data["resolved"])
        f.flight.report_generated = False
        db.session.commit()
        return jsonify({"ok": True, "is_complete": f.is_complete,
                        "incomplete": f.flight.incomplete_count,
                        "can_generate": f.flight.can_generate})

    @app.route("/flights/<int:flight_id>/summary", methods=["POST"])
    @requires("edit_findings")
    def summary_update(flight_id):
        """
        Store the agronomist's wording for one pattern on the summary page.

        Blank text removes the override, so clearing the box restores the
        assembled sentence rather than leaving an empty one in the report.

        This clears report_generated for the same reason editing a finding does:
        the document a farmer would receive is no longer the one on file.
        """
        flight = db.get_or_404(Flight, flight_id)
        data = request.get_json(force=True)
        category = (data.get("category") or "").strip()
        field = data.get("field")
        if category not in aggregation.CATEGORY_ORDER or field not in ("observation", "suggestion"):
            abort(400)

        text_value = (data.get("text") or "").strip()
        edit = SummaryEdit.query.filter_by(flight_id=flight.id, category=category).first()
        if edit is None:
            if not text_value:
                return jsonify({"ok": True, "edited": False})
            edit = SummaryEdit(flight_id=flight.id, category=category)
            db.session.add(edit)
        setattr(edit, field, text_value)
        edit.updated_by_id = current_user.id
        if not (edit.observation or "").strip() and not (edit.suggestion or "").strip():
            db.session.delete(edit)
            edited = False
        else:
            edited = bool(text_value)
        flight.report_generated = False
        db.session.commit()
        return jsonify({"ok": True, "edited": edited})

    @app.route("/findings/<int:finding_id>/comment", methods=["POST"])
    @requires("record_farmer_comment")
    def finding_comment(finding_id):
        """
        Record what the farmer said back about a finding.

        Kept apart from finding_update because the two answer to different
        permissions: customer success may record a farmer's words without being
        able to edit the finding those words are about.

        Saving here deliberately does NOT clear report_generated. The comment
        never appears in the report, so the generated PDF is still an accurate
        copy of what was sent, and invalidating it would force a pointless
        regeneration and re-send.
        """
        f = db.get_or_404(Finding, finding_id)
        data = request.get_json(force=True)
        comment = (data.get("farmer_comment") or "").strip()
        had = f.has_farmer_comment
        f.farmer_comment = comment
        if comment and not had:
            f.farmer_comment_at = datetime.utcnow()
            f.farmer_comment_by_id = current_user.id
        elif comment:
            f.farmer_comment_at = datetime.utcnow()
            f.farmer_comment_by_id = current_user.id
        else:
            f.farmer_comment_at = None
            f.farmer_comment_by_id = None
        db.session.commit()
        return jsonify({
            "ok": True,
            "has_comment": f.has_farmer_comment,
            "recorded_by": (f.farmer_comment_by.name if f.farmer_comment_by else ""),
            "recorded_at": (f.farmer_comment_at.strftime("%d %b %Y") if f.farmer_comment_at else ""),
            "flagged": f.flight.farmer_comment_count,
        })

    @app.route("/findings/<int:finding_id>/delete", methods=["POST"])
    @requires("edit_findings")
    def finding_delete(finding_id):
        f = db.get_or_404(Finding, finding_id)
        flight = f.flight
        db.session.delete(f)
        flight.report_generated = False
        db.session.commit()
        flash("Finding removed.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    # ---- report ----
    @app.route("/flights/<int:flight_id>/generate", methods=["POST"])
    @requires("generate_report")
    def flight_generate(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        if not flight.can_generate:
            # Say which of the two is missing, rather than blaming the findings
            # when it is the map that is absent.
            if flight.incomplete_count or not flight.findings:
                flash("Complete every finding first — each needs an observation, "
                      "cause and recommendation.", "warn")
            else:
                flash("Upload the annotated map snapshot first — the report's findings "
                      "are numbered against it.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        ok = _build_pdf(flight)
        flight.report_generated = True
        if flight.status == "Draft":
            flight.status = "Ready for Review"
        db.session.commit()
        if ok:
            flash("Report generated. Preview it, then approve and send.", "ok")
        else:
            flash("Report ready to preview. (Server PDF export will be available on the deployed app.)", "ok")
        return redirect(url_for("flight_report", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/report")
    @login_required
    def flight_report(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        base = _public_base()
        return render_template("report.html", public=False, base_url=base,
                               share=integrations.share_links(flight, base),
                               **report_context(flight))

    @app.route("/flights/<int:flight_id>/report.pdf")
    @login_required
    def flight_report_pdf(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        return _serve_pdf(flight, url_for("flight_report", flight_id=flight.id, print=1))

    @app.route("/flights/<int:flight_id>/send", methods=["POST"])
    @requires("deliver_report")
    def flight_send(flight_id):
        """Automatic delivery through the configured Email / WhatsApp provider."""
        flight = db.get_or_404(Flight, flight_id)
        if not flight.can_generate:
            flash("Complete the findings before sending.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        base = _public_base()
        pdf_bytes = _pdf_bytes(flight)
        pdf_name = flight.report_filename
        channels = request.form.getlist("channel")
        msgs, any_ok = [], False
        if "email" in channels:
            body = integrations.acre_voice_message(flight, "email", base)
            ok, m = integrations.send_email(
                flight.farm.farmer_email,
                f"Your Acre Insights field report — {flight.farm.name} (Flight {flight.flight_number})",
                body, pdf_bytes, pdf_name)
            flight.sent_email = flight.sent_email or ok
            any_ok = any_ok or ok
            msgs.append("Email: " + m)
        if "whatsapp" in channels:
            body = integrations.acre_voice_message(flight, "whatsapp", base)
            ok, m = integrations.send_whatsapp(flight.farm.farmer_phone, body)
            flight.sent_whatsapp = flight.sent_whatsapp or ok
            any_ok = any_ok or ok
            msgs.append("WhatsApp: " + m)
        if not channels:
            flash("Pick at least one channel to send on.", "warn")
        else:
            if any_ok:
                flight.sent_at = datetime.utcnow()
                flight.status = "Sent"
                flight.delivery_method = "api"
            db.session.commit()
            if any_ok:
                flash(" ".join(msgs), "ok")
            else:
                # Every provider refused. Say so plainly and point at the handoff
                # buttons, which need no provider account at all.
                flash(" ".join(msgs) + " Nothing was delivered automatically — "
                      "use Send from my WhatsApp or Send from my email instead.", "warn")
        return redirect(url_for("flight_report", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/mark-shared", methods=["POST"])
    @requires("deliver_report")
    def flight_mark_shared(flight_id):
        """
        Records a hand-off share: the report went out through the sender's own
        WhatsApp or mail client rather than through a provider API. Called by the
        share buttons the moment they open the external app.
        """
        flight = db.get_or_404(Flight, flight_id)
        channel = (request.get_json(silent=True) or {}).get("channel", "")
        if channel not in ("whatsapp", "email"):
            return jsonify({"ok": False, "error": "Unknown channel."}), 400
        if channel == "whatsapp":
            flight.sent_whatsapp = True
        else:
            flight.sent_email = True
        flight.sent_at = datetime.utcnow()
        flight.status = "Sent"
        flight.delivery_method = "handoff"
        db.session.commit()
        return jsonify({"ok": True, "channel": channel,
                        "at": flight.sent_at.strftime("%d %b %Y, %H:%M")})

    # ---- public farmer report + acknowledgement (no login) ----
    @app.route("/r/<token>")
    def public_report(token):
        flight = Flight.query.filter_by(share_token=token).first_or_404()
        return render_template("report.html", public=True, share=None,
                               base_url=_public_base(), **report_context(flight))

    @app.route("/r/<token>/ack", methods=["POST"])
    def public_ack(token):
        flight = Flight.query.filter_by(share_token=token).first_or_404()
        if not flight.acknowledged:
            flight.acknowledged = True
            flight.acknowledged_at = datetime.utcnow()
            db.session.commit()
        return jsonify({"ok": True, "at": flight.acknowledged_at.strftime("%d %b %Y, %H:%M")})

    @app.route("/r/<token>/report.pdf")
    def public_report_pdf(token):
        flight = Flight.query.filter_by(share_token=token).first_or_404()
        return _serve_pdf(flight, url_for("public_report", token=flight.share_token, print=1))

    # ---- people (admin) ----
    @app.route("/users")
    @admin_required
    def users():
        everyone = User.query.order_by(User.created_at.desc()).all()
        return render_template(
            "users.html",
            awaiting=[u for u in everyone if u.status == "pending"],
            approved=[u for u in everyone if u.status == "approved"],
            declined=[u for u in everyone if u.status == "rejected"])

    @app.route("/users/<int:user_id>/approve", methods=["POST"])
    @admin_required
    def user_approve(user_id):
        u = db.get_or_404(User, user_id)
        role = request.form.get("role", u.requested_role or "agronomist")
        if role not in ROLES:
            flash("Pick a valid role before approving.", "warn")
            return redirect(url_for("users"))
        u.role = role
        u.status = "approved"
        u.active = True
        u.approved_at = datetime.utcnow()
        u.approved_by_id = current_user.id
        u.decision_note = request.form.get("note", "").strip()
        db.session.commit()
        integrations.send_email(
            u.email,
            "Your SHAMBA Tracker account is approved",
            f"Hi {u.name.split()[0]}, your access to SHAMBA Tracker has been approved as "
            f"{ROLE_LABELS[role]}.\n\nSign in here: {url_for('login', _external=True)}\n\n— Acre Insights")
        flash(f"{u.name} approved as {ROLE_LABELS[role]}.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/reject", methods=["POST"])
    @admin_required
    def user_reject(user_id):
        u = db.get_or_404(User, user_id)
        if u.id == current_user.id:
            flash("You can't decline your own account.", "warn")
            return redirect(url_for("users"))
        u.status = "rejected"
        u.active = False
        u.decision_note = request.form.get("note", "").strip()
        u.approved_by_id = current_user.id
        db.session.commit()
        flash(f"Access request from {u.name} declined.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/new", methods=["POST"])
    @admin_required
    def user_new():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "agronomist")
        pw = request.form.get("password", "").strip()
        if not (name and email and pw):
            flash("Name, email and a temporary password are required.", "warn")
        elif len(pw) < 6:
            flash("Use a temporary password of at least 6 characters.", "warn")
        elif role not in ROLES:
            flash("Pick a valid role.", "warn")
        elif User.query.filter_by(email=email).first():
            flash("That email is already in use.", "warn")
        else:
            u = User(name=name, email=email, role=role, requested_role=role, active=True,
                     status="approved", approved_at=datetime.utcnow(),
                     approved_by_id=current_user.id,
                     job_title=request.form.get("job_title", "").strip(),
                     phone=request.form.get("phone", "").strip())
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            flash(f"Added {name} as {ROLE_LABELS[role]}.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/edit", methods=["POST"])
    @admin_required
    def user_edit(user_id):
        u = db.get_or_404(User, user_id)
        u.name = request.form.get("name", u.name).strip()
        email = request.form.get("email", "").strip().lower()
        if email and email != u.email:
            if User.query.filter(User.email == email, User.id != u.id).first():
                flash("Another account already uses that email.", "warn")
                return redirect(url_for("users"))
            u.email = email
        new_role = request.form.get("role", u.role)
        if new_role in ROLES:
            if (u.role == "admin" and new_role != "admin"
                    and User.query.filter_by(role="admin", status="approved").count() <= 1):
                flash("That is the only admin account — promote somebody else first.", "warn")
                return redirect(url_for("users"))
            u.role = new_role
        u.job_title = request.form.get("job_title", "").strip()
        u.phone = request.form.get("phone", "").strip()
        u.location = request.form.get("location", "").strip()
        pw = request.form.get("password", "").strip()
        if pw:
            if len(pw) < 6:
                flash("Use a password of at least 6 characters.", "warn")
                return redirect(url_for("users"))
            u.set_password(pw)
        db.session.commit()
        flash(f"{u.name} updated.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/toggle", methods=["POST"])
    @admin_required
    def user_toggle(user_id):
        u = db.get_or_404(User, user_id)
        if u.id == current_user.id:
            flash("You can't deactivate your own account.", "warn")
        elif (u.role == "admin" and u.active
              and User.query.filter_by(role="admin", status="approved", active=True).count() <= 1):
            flash("That is the only active admin — promote somebody else first.", "warn")
        else:
            u.active = not u.active
            db.session.commit()
            flash(f"{u.name} is now {'active' if u.active else 'inactive'}.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def user_delete(user_id):
        u = db.get_or_404(User, user_id)
        if u.id == current_user.id:
            flash("You can't delete your own account from here — use your profile page.", "warn")
        elif (u.role == "admin" and u.status == "approved"
              and User.query.filter_by(role="admin", status="approved").count() <= 1):
            flash("That is the only admin account — promote somebody else first.", "warn")
        else:
            name = u.name
            User.query.filter_by(approved_by_id=u.id).update({"approved_by_id": None})
            db.session.delete(u)
            db.session.commit()
            flash(f"{name} removed.", "ok")
        return redirect(url_for("users"))

    # ---- serve uploaded images ----
    @app.route("/uploads/<path:name>")
    def uploaded(name):
        # Streamed through the application rather than redirected to a signed
        # URL, so the bucket stays private and a link embedded in a report can
        # never expire.
        safe = secure_filename(name)
        data = STORE.read(safe)
        if data is None:
            abort(404)
        mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        return send_file(io.BytesIO(data), mimetype=mime,
                         download_name=safe, max_age=3600)

    # ---- errors ----
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               msg="Your role doesn't include access to that page."), 403

    @app.errorhandler(404)
    def notfound(e):
        return render_template("error.html", code=404,
                               msg="We couldn't find that page."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413,
                               msg="That file is larger than the 25 MB upload limit."), 413


# ------------------------------------------------------------------ homepage
def asset_url(value):
    """
    Resolve a homepage image to a URL.

    A value shipped with the project ("img/hero-map.jpg") is served from static/.
    One an admin has uploaded ("uploads/home-hero-image.png") is served from the
    store, which is where it survives a redeploy.
    """
    value = (value or "").strip()
    if value.startswith("uploads/"):
        return url_for("uploaded", name=value[len("uploads/"):])
    return url_for("static", filename=value)


def site_content():
    """
    Homepage defaults overlaid with whatever an admin has saved.

    Read on every request that renders a template, so it must not raise. A
    database that predates the table (or is mid-migration) falls back to the
    defaults rather than taking the public homepage down with it.
    """
    values = dict(homepage.DEFAULTS)
    try:
        for row in SiteContent.query.all():
            if row.key in values and row.value is not None:
                values[row.key] = row.value
    except SQLAlchemyError:
        # Only a database problem is tolerated here — a table that does not exist
        # yet on a database mid-migration. Catching everything would hide a real
        # bug behind a page that quietly renders its defaults.
        db.session.rollback()
    return values


# ------------------------------------------------------------------ bulk import
def _plan_import(kind, headers, rows):
    """
    Ask bulk_import what the sheet would do, handing it the lookups it needs.

    The database work lives here so bulk_import stays query-free and testable
    on plain data, the same way parsing.py and report_data.py do.
    """
    if kind == "farms":
        existing = {f.name.strip().lower(): f.id
                    for f in Farm.query.all() if (f.name or "").strip()}
        return bulk_import.plan_farms(headers, rows, existing)

    farms_by_name = {f.name.strip().lower(): f.id
                     for f in Farm.query.all() if (f.name or "").strip()}
    flight_keys = {(fl.farm_id, (fl.season or "").strip().lower(), fl.flight_number): fl.id
                   for fl in Flight.query.all()}
    return bulk_import.plan_flights(headers, rows, farms_by_name, flight_keys)


def _set_if_given(obj, attr, raw, convert=None):
    """
    Write a value only when the cell had something in it.

    A blank cell on an update means "leave this alone". A part-filled sheet is
    how somebody fixes two phone numbers across forty farms, and treating those
    blanks as deletions would quietly empty every other column.
    """
    if raw is None or str(raw).strip() == "":
        return
    setattr(obj, attr, convert(raw) if convert else str(raw).strip())


def _apply_farm(plan, created, updated):
    v = plan["fields"]
    if plan["action"] == "create":
        farm = Farm(name=v["name"].strip())
        db.session.add(farm)
        created += 1
    else:
        farm = db.session.get(Farm, plan["target_id"])
        if farm is None:                       # deleted while the preview was open
            return created, updated
        updated += 1

    for field in ("crop", "location", "farmer_name", "farmer_email",
                  "farmer_phone", "dronedeploy_project_url", "notes"):
        _set_if_given(farm, field, v.get(field))
    _set_if_given(farm, "acreage", v.get("acreage"), bulk_import.clean_float)
    return created, updated


def _apply_flight(plan, created, updated):
    v = plan["fields"]
    farm = db.session.get(Farm, plan["farm_id"])
    if farm is None:
        return created, updated

    if plan["action"] == "create":
        flight = Flight(
            farm_id=farm.id,
            season=v["season"].strip(),
            flight_number=bulk_import.clean_int(v.get("flight_number")),
            # The same defaults the flight form applies, so a sheet that leaves
            # these blank produces the flight the form would have produced.
            flights_planned=bulk_import.clean_int(v.get("flights_planned")) or 1,
            crop=(v.get("crop") or "").strip() or farm.crop,
            acreage=bulk_import.clean_float(v.get("acreage")) or farm.acreage,
            flight_date=bulk_import.clean_date(v.get("flight_date")),
            dronedeploy_project_url=(v.get("dronedeploy_project_url") or "").strip(),
            status=bulk_import.clean_status(v.get("status")) or "Draft",
        )
        db.session.add(flight)
        created += 1
        return created, updated

    flight = db.session.get(Flight, plan["target_id"])
    if flight is None:
        return created, updated
    updated += 1
    for field in ("crop", "dronedeploy_project_url"):
        _set_if_given(flight, field, v.get(field))
    _set_if_given(flight, "flights_planned", v.get("flights_planned"), bulk_import.clean_int)
    _set_if_given(flight, "acreage", v.get("acreage"), bulk_import.clean_float)
    _set_if_given(flight, "flight_date", v.get("flight_date"), bulk_import.clean_date)
    _set_if_given(flight, "status", v.get("status"), bulk_import.clean_status)
    return created, updated


def _notify_admins_of_signup(u):
    """Best-effort note to every admin that somebody is waiting. Never blocks signup."""
    try:
        link = url_for("users", _external=True)
    except Exception:
        link = "/users"
    for admin in User.query.filter_by(role="admin", status="approved").all():
        integrations.send_email(
            admin.email,
            f"New SHAMBA Tracker access request — {u.name}",
            f"{u.name} ({u.email}) has asked to join SHAMBA Tracker as "
            f"{u.requested_role_label}.\n\nApprove or decline: {link}\n\n— SHAMBA Tracker")


def _public_base():
    """
    The base URL used inside shared links. PUBLIC_BASE_URL wins when set;
    otherwise it comes from the request, which ProxyFix has already corrected
    to the public scheme and host.

    A link that goes out to a farmer gets one chance to work, so this also
    forces https on anything that is not a local address: WhatsApp and several
    mail clients quietly refuse to make a plain-http link tappable.
    """
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        if not configured.startswith(("http://", "https://")):
            configured = "https://" + configured
        return configured

    base = request.url_root.rstrip("/")
    host = request.host.split(":")[0].lower()
    is_local = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local")
    if base.startswith("http://") and not is_local:
        base = "https://" + base[len("http://"):]
    return base


# ------------------------------------------------------------------ PDF glue
@contextmanager
def _map_path(flight):
    """
    A real path to the flight's map for the duration of a render.

    The renderer inlines the image as base64 and reads it from disk, so an
    object stored remotely is written to a temporary file and removed again as
    soon as the render is done.
    """
    if not flight.map_image:
        yield None
        return
    with STORE.local_path(flight.map_image) as p:
        yield p


# Cached renders live below the uploads folder rather than in it: /uploads/<name>
# is public and runs the name through secure_filename, which flattens away path
# separators, so nothing under this directory is reachable through that route.
PDF_CACHE_DIR = os.path.join(UPLOAD_DIR, ".cache")


def _pdf_cache_path(flight):
    try:
        os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    except OSError:
        return None
    return os.path.join(PDF_CACHE_DIR, _pdf_cache_key(flight))


def _pdf_cache_key(flight):
    """
    Identifies the exact content of this report. Any edit to the flight, its
    farm or any finding changes the key, so a cached file can never outlive the
    data it was built from.
    """
    import hashlib
    parts = [str(flight.id), flight.season or "", str(flight.flight_number),
             str(flight.flights_planned), flight.crop or "", str(flight.acreage),
             flight.flight_date.isoformat() if flight.flight_date else "",
             flight.status or "", flight.farm.name or "", str(flight.farm.acreage),
             flight.map_image or "", flight.agronomist_note or ""]
    for f in flight.findings:
        parts += [str(f.id), f.category or "", f.colour_meaning or "", f.observation or "",
                  f.likely_cause or "", f.recommendation or "", str(f.area_acres),
                  str(f.resolved)]
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"report-{flight.id}-{digest}.pdf"


def _pdf_bytes(flight, use_cache=True):
    """
    Render the report to PDF bytes, or None if PDF can't be produced here.

    This renders the same report_doc.html the browser shows, wrapped in a bare
    page shell. Screen and paper therefore run on one stylesheet and one set of
    page breaks, which is the only way the two can be guaranteed to match.

    A render costs about a second, which is long enough that a second click
    feels like the download did nothing, so the result is cached against a key
    derived from the report's own content.
    """
    if not pdf_gen.PDF_AVAILABLE:
        print("[pdf] WeasyPrint unavailable — falling back to the browser print path")
        return None

    cached = _pdf_cache_path(flight) if use_cache else None
    if cached and os.path.exists(cached):
        try:
            with open(cached, "rb") as fh:
                return fh.read()
        except OSError:
            pass                                   # unreadable cache is not a failure

    try:
        logo = os.path.join(BASE_DIR, "static", "img", "acre-logo.png")
        # The map is held on disk only for the length of the render; with object
        # storage it is a temporary file that is removed on the way out.
        with _map_path(flight) as map_file:
            html = render_template("report_print.html",
                                   pdf=True, public=True, share=None,
                                   base_url=_public_base_safe(),
                                   logo_uri=pdf_gen.data_uri(logo),
                                   map_uri=pdf_gen.data_uri(map_file),
                                   **report_context(flight))
            data = pdf_gen.render_pdf(html, base_url=BASE_DIR)
    except Exception as exc:                      # never 500 on a report download
        import traceback
        print("[pdf] render failed:", exc)
        traceback.print_exc()
        return None

    if cached:
        try:
            _prune_pdf_cache(flight.id)
            with open(cached, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            print("[pdf] could not cache:", exc)   # serving still succeeds
    return data


def _prune_pdf_cache(flight_id):
    """Drop this flight's older cached renders so edits don't pile up on disk."""
    import glob
    for path in glob.glob(os.path.join(PDF_CACHE_DIR, f"report-{flight_id}-*.pdf")):
        try:
            os.remove(path)
        except OSError:
            pass


def _public_base_safe():
    """_public_base() outside a request context still returns something usable."""
    try:
        return _public_base()
    except RuntimeError:
        return os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")


def _build_pdf(flight):
    data = _pdf_bytes(flight)
    if data is None:
        return False
    name = f"{flight.slug}.pdf"          # ASCII-safe name for the store
    STORE.save_bytes(data, name)
    flight.report_pdf = name
    return True


def _content_disposition(filename):
    """
    A Content-Disposition that survives real farm names.

    The plain `filename` parameter is limited to Latin-1, so anything outside it
    is stripped for that copy and the full UTF-8 name is carried in `filename*`
    (RFC 5987), which every current browser prefers.
    """
    from urllib.parse import quote
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "Field Report.pdf"
    ascii_name = ascii_name.replace('"', "")
    return (f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def _serve_pdf(flight, fallback_url):
    data = _pdf_bytes(flight)
    filename = flight.report_filename          # Farm Name_Crop Name_Season Year_Flight No.pdf
    if data is None:
        # Server-side PDF isn't available on this host. The report itself is the
        # same document WeasyPrint would have rendered, so the browser's own
        # "Save as PDF" produces the same pages under the same name.
        return redirect(fallback_url)
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": _content_disposition(filename),
                             "Content-Length": str(len(data)),
                             "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, max-age=0, must-revalidate"})


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
