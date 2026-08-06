"""
SHAMBA Tracker — Flight Report Automation
A product of Acre Insights.

Flask + SQLite/Postgres + Tailwind (CDN) + vanilla JS. Ready for Railway (see Dockerfile).
Run locally:  python app.py     (creates the DB and seeds the first admin)
"""
import os
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, abort, send_file, Response)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError, OperationalError

from models import (db, User, Farm, Flight, Finding, ROLES, ROLE_LABELS,
                    ROLE_BLURB, ROLE_DASHBOARD, STATUS_LABELS, slugify)
import parsing
import integrations
import pdf_gen
import schema

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_CSV = {".csv"}
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}

# Who may do what. Everything not listed here is open to any signed-in user.
PERMISSIONS = {
    "manage_users":   {"admin"},
    "manage_farms":   {"admin", "agronomist", "field_operator"},
    "manage_flights": {"admin", "agronomist", "field_operator"},
    "edit_findings":  {"admin", "agronomist"},
    "generate_report": {"admin", "agronomist"},
    "deliver_report": {"admin", "agronomist", "customer_success"},
}


def create_app():
    app = Flask(__name__)
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
            "ROLE_LABELS": ROLE_LABELS,
            "ROLE_BLURB": ROLE_BLURB,
            "STATUS_LABELS": STATUS_LABELS,
            "ROLES": ROLES,
            "can": can,
            "pending_count": _pending_count(),
            "now": datetime.utcnow(),
        }

    register_routes(app)

    with app.app_context():
        init_db()

    return app


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
    path = os.path.join(UPLOAD_DIR, name)
    file_storage.save(path)
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
    """Everything the report templates need, incl. prior-flight comparison."""
    prev = (Flight.query
            .filter_by(farm_id=flight.farm_id, season=flight.season,
                       flight_number=flight.flight_number - 1)
            .first())
    resolved = still_open = prev_total = None
    if prev:
        prev_total = len(prev.findings)
        resolved = sum(1 for f in flight.findings if f.resolved)
        still_open = len(flight.findings) - resolved
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
    }


# ------------------------------------------------------------------ routes
def register_routes(app):

    # ---- landing ----
    @app.route("/")
    def home():
        if current_user.is_authenticated:
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
                # Correct credentials, but the account is still in the queue.
                # Say so plainly rather than reusing the wrong-password message.
                reason = {
                    "kind": "pending",
                    "title": "Your account is waiting for approval",
                    "body": ("Your details are correct, but an admin has not approved this "
                             "account yet. You will get an email as soon as they do."),
                    "email": u.email,
                }
                return render_template("login.html", reason=reason)
            elif u.status == "rejected":
                note = (u.decision_note or "").strip()
                reason = {
                    "kind": "rejected",
                    "title": "This request for access was declined",
                    "body": ("An admin declined this request"
                             + (f": {note}" if note else ".")
                             + " Speak to your workspace admin if you think that is wrong."),
                    "email": u.email,
                }
                return render_template("login.html", reason=reason)
            elif not u.active:
                reason = {
                    "kind": "inactive",
                    "title": "This account has been deactivated",
                    "body": ("The account exists but has been switched off. A workspace admin "
                             "can turn it back on from the People page."),
                    "email": u.email,
                }
                return render_template("login.html", reason=reason)
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
        farm.dronedeploy_project_url = request.form.get("dronedeploy_project_url", "").strip()
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
            dronedeploy_project_url=request.form.get("dronedeploy_project_url", "").strip()
                or farm.dronedeploy_project_url,
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
                    colour_meaning="Pending review", colour_swatch="#6E8659",
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
            flash("Complete every finding first — each needs an observation, cause and recommendation.", "warn")
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
        pdf_name = f"{flight.slug}.pdf"
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
        safe = secure_filename(name)
        path = os.path.join(UPLOAD_DIR, safe)
        if not os.path.exists(path):
            abort(404)
        return send_file(path)

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
    """The base URL used inside shared links. PUBLIC_BASE_URL wins when set."""
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return configured or request.url_root.rstrip("/")


# ------------------------------------------------------------------ PDF glue
def _map_path(flight):
    if flight.map_image:
        p = os.path.join(UPLOAD_DIR, flight.map_image)
        if os.path.exists(p):
            return p
    return None


def _pdf_bytes(flight):
    """Render the report to PDF bytes, or None if PDF can't be produced here."""
    if not pdf_gen.PDF_AVAILABLE:
        return None
    try:
        logo = os.path.join(BASE_DIR, "static", "img", "acre-logo.png")
        html = render_template("report_pdf.html",
                               logo_uri=pdf_gen.data_uri(logo),
                               map_uri=pdf_gen.data_uri(_map_path(flight)),
                               font_css=pdf_gen.font_css(),
                               **report_context(flight))
        return pdf_gen.render_pdf(html, base_url=BASE_DIR)
    except Exception as exc:                      # never 500 on a report download
        import traceback
        print("PDF render failed:", exc)
        traceback.print_exc()
        return None


def _build_pdf(flight):
    data = _pdf_bytes(flight)
    if data is None:
        return False
    name = f"{flight.slug}.pdf"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
        fh.write(data)
    flight.report_pdf = name
    return True


def _serve_pdf(flight, fallback_url):
    """
    Send the PDF as a download named Farm_Crop_SeasonYear.pdf.

    The fallback only exists for hosts without WeasyPrint's native libraries.
    It is a real degradation: the browser print dialog drops background colours
    unless the user ticks "Background graphics", so the print template forces
    `print-color-adjust: exact` to keep the colour code readable either way.
    """
    data = _pdf_bytes(flight)
    filename = f"{flight.slug}.pdf"
    if data is None:
        return redirect(fallback_url)
    return Response(data, mimetype="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                        "Content-Length": str(len(data)),
                        "Cache-Control": "no-store",
                    })


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
