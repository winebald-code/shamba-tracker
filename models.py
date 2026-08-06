"""SQLAlchemy models for SHAMBA Tracker."""
import secrets
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# --------------------------------------------------------------------- roles
ROLES = ["admin", "agronomist", "customer_success", "field_operator"]

ROLE_LABELS = {
    "admin": "Admin",
    "agronomist": "Agronomist",
    "customer_success": "Customer Success",
    "field_operator": "Field Operator",
}

# Shown on the approval screen and the role picker so an admin knows what
# they are granting before they grant it.
ROLE_BLURB = {
    "admin": "Full access, plus approving new accounts and managing people.",
    "agronomist": "Completes findings, writes the cause and the recommendation, generates reports.",
    "customer_success": "Reviews finished reports and delivers them to the farmer.",
    "field_operator": "Records flights and uploads the map and the DroneDeploy export.",
}

# Which dashboard each role lands on after signing in.
ROLE_DASHBOARD = {
    "admin": "dashboard_admin",
    "agronomist": "dashboard_agronomist",
    "customer_success": "dashboard_cs",
    "field_operator": "dashboard_operator",
}

STATUSES = ["pending", "approved", "rejected"]
STATUS_LABELS = {
    "pending": "Awaiting approval",
    "approved": "Approved",
    "rejected": "Declined",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="agronomist", nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ---- profile ----
    phone = db.Column(db.String(60), default="")
    job_title = db.Column(db.String(120), default="")
    location = db.Column(db.String(160), default="")
    bio = db.Column(db.Text, default="")

    # ---- approval workflow ----
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    requested_role = db.Column(db.String(30), default="agronomist")
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decision_note = db.Column(db.Text, default="")
    last_login_at = db.Column(db.DateTime)

    approved_by = db.relationship("User", remote_side=[id], uselist=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.replace("_", " ").title())

    @property
    def requested_role_label(self):
        return ROLE_LABELS.get(self.requested_role, "Agronomist")

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def initials(self):
        parts = [p for p in (self.name or "").split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "U"

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def dashboard_endpoint(self):
        return ROLE_DASHBOARD.get(self.role, "dashboard_agronomist")

    # Flask-Login: only approved, active accounts may hold a session.
    @property
    def is_active(self):
        return bool(self.active and self.status == "approved")


class Farm(db.Model):
    __tablename__ = "farms"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    crop = db.Column(db.String(120), default="")
    acreage = db.Column(db.Float)
    location = db.Column(db.String(200), default="")
    farmer_name = db.Column(db.String(160), default="")
    farmer_email = db.Column(db.String(160), default="")
    farmer_phone = db.Column(db.String(60), default="")
    dronedeploy_project_url = db.Column(db.String(500), default="")
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    flights = db.relationship(
        "Flight", backref="farm", cascade="all, delete-orphan",
        order_by="Flight.created_at.desc()"
    )


class Flight(db.Model):
    __tablename__ = "flights"
    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False)
    season = db.Column(db.String(30), nullable=False)          # e.g. 2026LR
    flight_number = db.Column(db.Integer, nullable=False)
    flights_planned = db.Column(db.Integer, default=1)
    crop = db.Column(db.String(120), default="")
    acreage = db.Column(db.Float)
    flight_date = db.Column(db.Date)
    status = db.Column(db.String(40), default="Draft")         # Draft / Ready for Review / Approved / Sent
    map_image = db.Column(db.String(300), default="")          # filename in uploads/
    csv_filename = db.Column(db.String(300), default="")
    dronedeploy_project_url = db.Column(db.String(500), default="")

    report_generated = db.Column(db.Boolean, default=False)
    report_pdf = db.Column(db.String(300), default="")         # filename in uploads/
    agronomist_note = db.Column(db.Text, default="")

    share_token = db.Column(db.String(48), unique=True, index=True, default=lambda: secrets.token_urlsafe(16))
    sent_email = db.Column(db.Boolean, default=False)
    sent_whatsapp = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime)
    delivery_method = db.Column(db.String(40), default="")     # "api" or "handoff"
    acknowledged = db.Column(db.Boolean, default=False)
    acknowledged_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    findings = db.relationship(
        "Finding", backref="flight", cascade="all, delete-orphan",
        order_by="Finding.sort_order"
    )

    # ---- helpers ----
    @property
    def slug(self):
        """
        The download filename: Farm_Crop_SeasonYear.

        The season already carries the year in Acre's convention (2026LR), but
        a season typed as just "LR" would produce an ambiguous filename, so the
        year is appended when it is missing.
        """
        crop = (self.crop or self.farm.crop or "Crop").strip()
        season = (self.season or "").strip()
        dated = self.flight_date or (self.created_at.date() if self.created_at else None)
        year = str(dated.year) if dated else str(datetime.utcnow().year)
        if year not in season:
            season = f"{season}{year}" if season else year
        return slugify(f"{self.farm.name}_{crop}_{season}")

    @property
    def complete_findings(self):
        return [f for f in self.findings if f.is_complete]

    @property
    def incomplete_count(self):
        return sum(1 for f in self.findings if not f.is_complete)

    @property
    def can_generate(self):
        return len(self.findings) > 0 and self.incomplete_count == 0

    @property
    def is_delivered(self):
        return bool(self.sent_email or self.sent_whatsapp)

    def category_counts(self):
        counts = {}
        for f in self.findings:
            counts[f.category] = counts.get(f.category, 0) + 1
        return counts

    def meaning_counts(self):
        counts = {}
        for f in self.findings:
            counts[f.colour_meaning] = counts.get(f.colour_meaning, 0) + 1
        return counts


class Finding(db.Model):
    __tablename__ = "findings"
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey("flights.id"), nullable=False)
    annotation_id = db.Column(db.String(80), default="")
    label_hex = db.Column(db.String(20), default="")
    colour_swatch = db.Column(db.String(20), default="#6E8659")
    colour_meaning = db.Column(db.String(40), default="Pending review")
    category = db.Column(db.String(60), default="Needs Investigation")
    observation = db.Column(db.Text, default="")
    likely_cause = db.Column(db.Text, default="")
    recommendation = db.Column(db.Text, default="")
    area_text = db.Column(db.String(40), default="")
    area_acres = db.Column(db.Float)
    measurement_type = db.Column(db.String(40), default="")
    geometry_type = db.Column(db.String(40), default="")
    annotation_link = db.Column(db.String(600), default="")
    resolved = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_complete(self):
        return bool(self.category and self.observation.strip()
                    and self.likely_cause.strip() and self.recommendation.strip())


def slugify(text):
    import re
    text = (text or "").strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_") or "Report"
