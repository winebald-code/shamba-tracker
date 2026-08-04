"""
SHAMBA Tracker — Flight Report Automation
A product of Acre Insights.

Flask + SQLite + Tailwind (CDN) + vanilla JS. Ready for Railway (see Dockerfile).
Run locally:  python app.py     (creates the DB and seeds the first admin, Cathy)
"""
import os
from datetime import datetime, date
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, abort, send_file, Response)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError, OperationalError

from models import db, User, Farm, Flight, Finding, ROLES, ROLE_LABELS, slugify
import parsing
import integrations
import pdf_gen

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_CSV = {".csv"}
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
    db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "shamba.db"))
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

    # make colour code + categories available to every template
    @app.context_processor
    def inject_globals():
        return {
            "COLOUR_CODE": parsing.COLOUR_CODE,
            "CATEGORIES": parsing.CATEGORIES,
            "ROLE_LABELS": ROLE_LABELS,
            "now": datetime.utcnow(),
        }

    register_routes(app)

    with app.app_context():
        init_db()

    return app


def init_db():
    """
    Create tables and seed the first admin — safely, even when several gunicorn
    workers start at once against a fresh database. Table creation is ordered,
    so the worker that wins the first CREATE completes them all; the others hit
    "already exists" (OperationalError) and simply move on.
    """
    try:
        db.create_all()
    except OperationalError:
        db.session.rollback()   # another worker created the tables first
    seed_admin()


def seed_admin():
    """
    Create the first admin (Cathy) if there are no users yet.

    Safe under multiple gunicorn workers booting at once: if another worker
    inserts the same admin a moment earlier, the duplicate insert raises
    IntegrityError, which we roll back and ignore instead of crashing the worker.
    """
    email = os.environ.get("ADMIN_EMAIL", "cathy@acre-insights.com")
    if User.query.first() is not None or User.query.filter_by(email=email).first() is not None:
        return
    pw = os.environ.get("ADMIN_PASSWORD", "AcreInsights2026")
    cathy = User(name=os.environ.get("ADMIN_NAME", "Cathy"), email=email, role="admin", active=True)
    cathy.set_password(pw)
    db.session.add(cathy)
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


# ------------------------------------------------------------------ helpers
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper


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
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            if not (name and email and pw):
                flash("Fill in every field to create your account.", "warn")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists. Try signing in.", "warn")
            else:
                # first ever user becomes admin, otherwise agronomist by default
                role = "admin" if User.query.count() == 0 else "agronomist"
                u = User(name=name, email=email, role=role, active=True)
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
                login_user(u)
                flash(f"Welcome to SHAMBA Tracker, {name.split()[0]}.", "ok")
                return redirect(url_for("dashboard"))
        return render_template("signup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            u = User.query.filter_by(email=email).first()
            if u and u.check_password(pw) and u.active:
                login_user(u)
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Those details didn't match, or the account is inactive.", "warn")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("home"))

    # ---- dashboard ----
    @app.route("/dashboard")
    @login_required
    def dashboard():
        farms = Farm.query.order_by(Farm.name).all()
        flights = Flight.query.all()
        findings = Finding.query.all()
        cat_counts = {}
        for f in findings:
            cat_counts[f.category] = cat_counts.get(f.category, 0) + 1
        stats = {
            "farms": len(farms),
            "flights": len(flights),
            "issues": len(findings),
            "reports": sum(1 for fl in flights if fl.report_generated),
            "sent": sum(1 for fl in flights if fl.sent_email or fl.sent_whatsapp),
            "acknowledged": sum(1 for fl in flights if fl.acknowledged),
        }
        recent = Flight.query.order_by(Flight.created_at.desc()).limit(6).all()
        return render_template("dashboard.html", stats=stats, cat_counts=cat_counts,
                               recent=recent, farms=farms)

    # ---- farms ----
    @app.route("/farms")
    @login_required
    def farms():
        return render_template("farms.html", farms=Farm.query.order_by(Farm.name).all())

    @app.route("/farms/new", methods=["POST"])
    @login_required
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
    @login_required
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
    @login_required
    def farm_delete(farm_id):
        farm = db.get_or_404(Farm, farm_id)
        db.session.delete(farm)
        db.session.commit()
        flash("Farm removed.", "ok")
        return redirect(url_for("farms"))

    # ---- flights ----
    @app.route("/farms/<int:farm_id>/flights/new", methods=["POST"])
    @login_required
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
    @login_required
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
    @login_required
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
            # Ready-for-Review notifies CS (simulated unless email keys are set)
            if new == "Ready for Review":
                admin = User.query.filter_by(role="admin").first()
                to = admin.email if admin else os.environ.get("ADMIN_EMAIL", "")
                subject = f"{flight.farm.name} ({flight.season}) — Flight {flight.flight_number}/{flight.flights_planned} ready for review"
                body = (f"{flight.farm.name}, {flight.season}, Flight {flight.flight_number} of "
                        f"{flight.flights_planned} is annotated and ready for review.\n\n"
                        f"Open it: {url_for('flight_detail', flight_id=flight.id, _external=True)}")
                integrations.send_email(to, subject, body)
                flash("Marked ready — Customer Success has been notified.", "ok")
            else:
                flash(f"Status set to {new}.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    @app.route("/flights/<int:flight_id>/delete", methods=["POST"])
    @login_required
    def flight_delete(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        farm_id = flight.farm_id
        db.session.delete(flight)
        db.session.commit()
        flash("Flight removed.", "ok")
        return redirect(url_for("farm_detail", farm_id=farm_id))

    @app.route("/flights/<int:flight_id>/note", methods=["POST"])
    @login_required
    def flight_note(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        flight.agronomist_note = request.form.get("note", "").strip()
        db.session.commit()
        flash("Note saved.", "ok")
        return redirect(url_for("flight_detail", flight_id=flight.id))

    # ---- findings CRUD (AJAX) ----
    @app.route("/flights/<int:flight_id>/findings/add", methods=["POST"])
    @login_required
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
    @login_required
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
    @login_required
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
    @login_required
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
        return render_template("report.html", public=False,
                               base_url=request.url_root.rstrip("/"), **report_context(flight))

    @app.route("/flights/<int:flight_id>/report.pdf")
    @login_required
    def flight_report_pdf(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        return _serve_pdf(flight, url_for("flight_report", flight_id=flight.id, print=1))

    @app.route("/flights/<int:flight_id>/send", methods=["POST"])
    @login_required
    def flight_send(flight_id):
        flight = db.get_or_404(Flight, flight_id)
        if not flight.can_generate:
            flash("Complete the findings before sending.", "warn")
            return redirect(url_for("flight_detail", flight_id=flight.id))
        base = request.url_root.rstrip("/")
        pdf_bytes = _pdf_bytes(flight)
        pdf_name = f"{flight.slug}.pdf"
        channels = request.form.getlist("channel")
        msgs = []
        if "email" in channels:
            body = integrations.acre_voice_message(flight, "email", base)
            ok, m = integrations.send_email(
                flight.farm.farmer_email,
                f"Your Acre Insights field report — {flight.farm.name} (Flight {flight.flight_number})",
                body, pdf_bytes, pdf_name)
            flight.sent_email = flight.sent_email or ok
            msgs.append("Email: " + m)
        if "whatsapp" in channels:
            body = integrations.acre_voice_message(flight, "whatsapp", base)
            ok, m = integrations.send_whatsapp(flight.farm.farmer_phone, body)
            flight.sent_whatsapp = flight.sent_whatsapp or ok
            msgs.append("WhatsApp: " + m)
        if channels:
            flight.sent_at = datetime.utcnow()
            flight.status = "Sent"
            db.session.commit()
            flash(" ".join(msgs), "ok")
        else:
            flash("Pick at least one channel to send on.", "warn")
        return redirect(url_for("flight_report", flight_id=flight.id))

    # ---- public farmer report + acknowledgement (no login) ----
    @app.route("/r/<token>")
    def public_report(token):
        flight = Flight.query.filter_by(share_token=token).first_or_404()
        return render_template("report.html", public=True,
                               base_url=request.url_root.rstrip("/"), **report_context(flight))

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

    # ---- users management (admin) ----
    @app.route("/users")
    @login_required
    @admin_required
    def users():
        return render_template("users.html", users=User.query.order_by(User.created_at).all())

    @app.route("/users/new", methods=["POST"])
    @login_required
    @admin_required
    def user_new():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "agronomist")
        pw = request.form.get("password", "").strip()
        if not (name and email and pw):
            flash("Name, email and a temporary password are required.", "warn")
        elif role not in ROLES:
            flash("Pick a valid role.", "warn")
        elif User.query.filter_by(email=email).first():
            flash("That email is already in use.", "warn")
        else:
            u = User(name=name, email=email, role=role, active=True)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            flash(f"Added {name} as {ROLE_LABELS[role]}.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/edit", methods=["POST"])
    @login_required
    @admin_required
    def user_edit(user_id):
        u = db.get_or_404(User, user_id)
        u.name = request.form.get("name", u.name).strip()
        new_role = request.form.get("role", u.role)
        if new_role in ROLES:
            u.role = new_role
        pw = request.form.get("password", "").strip()
        if pw:
            u.set_password(pw)
        db.session.commit()
        flash("User updated.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/toggle", methods=["POST"])
    @login_required
    @admin_required
    def user_toggle(user_id):
        u = db.get_or_404(User, user_id)
        if u.id == current_user.id:
            flash("You can't deactivate your own account.", "warn")
        else:
            u.active = not u.active
            db.session.commit()
            flash(f"{u.name} is now {'active' if u.active else 'inactive'}.", "ok")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    def user_delete(user_id):
        u = db.get_or_404(User, user_id)
        if u.id == current_user.id:
            flash("You can't delete your own account.", "warn")
        else:
            db.session.delete(u)
            db.session.commit()
            flash("User removed.", "ok")
        return redirect(url_for("users"))

    # ---- serve uploaded images ----
    @app.route("/uploads/<path:name>")
    def uploaded(name):
        path = os.path.join(UPLOAD_DIR, name)
        if not os.path.exists(path):
            abort(404)
        return send_file(path)

    # ---- errors ----
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               msg="You don't have access to that page."), 403

    @app.errorhandler(404)
    def notfound(e):
        return render_template("error.html", code=404,
                               msg="We couldn't find that page."), 404


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
    data = _pdf_bytes(flight)
    filename = f"{flight.slug}.pdf"
    if data is None:
        # Server-side PDF isn't available (e.g. WeasyPrint libraries missing on
        # this host): fall back to the print-optimised report, which the browser
        # can save as a correctly-named PDF.
        return redirect(fallback_url)
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def finding_json(f):
    return {
        "id": f.id, "annotation_id": f.annotation_id, "label_hex": f.label_hex,
        "colour_swatch": f.colour_swatch, "colour_meaning": f.colour_meaning,
        "category": f.category, "observation": f.observation,
        "likely_cause": f.likely_cause, "recommendation": f.recommendation,
        "area_text": f.area_text, "annotation_link": f.annotation_link,
        "is_complete": f.is_complete,
    }


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
