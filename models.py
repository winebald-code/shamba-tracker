"""SQLAlchemy models for SHAMBA Tracker."""
import secrets
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

ROLES = ["admin", "agronomist", "customer_success"]
ROLE_LABELS = {
    "admin": "Admin",
    "agronomist": "Agronomist",
    "customer_success": "Customer Success",
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

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.title())

    @property
    def initials(self):
        parts = [p for p in self.name.split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "U"

    # Flask-Login: inactive users can't log in
    @property
    def is_active(self):
        return self.active


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
        return slugify(f"{self.farm.name}_{self.crop or self.farm.crop}_{self.season}")

    @property
    def complete_findings(self):
        return [f for f in self.findings if f.is_complete]

    @property
    def incomplete_count(self):
        return sum(1 for f in self.findings if not f.is_complete)

    @property
    def can_generate(self):
        return len(self.findings) > 0 and self.incomplete_count == 0

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
    colour_swatch = db.Column(db.String(20), default="#6C6C6C")
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
