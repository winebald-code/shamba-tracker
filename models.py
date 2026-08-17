"""SQLAlchemy models for SHAMBA Tracker."""
import re
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
        """Underscored, ASCII-safe. Used for filenames written to disk."""
        return slugify(f"{self.farm.name}_{self.crop or self.farm.crop}_{self.season}"
                       f"_F{self.flight_number}")

    @property
    def report_basename(self):
        """
        What the farmer sees when the PDF lands in their downloads:
        `Farm Name_Crop Name_Season Year_Flight No`. Spaces inside each part are
        kept, so the parts stay legible; only the separators are underscores.

        The flight number is part of the name because the same farm, crop and
        season recur on every flight of a season — without it, each new report
        overwrites the last one in the farmer's downloads folder.
        """
        parts = [
            (self.farm.name or "Farm").strip(),
            (self.crop or self.farm.crop or "Crop").strip(),
            (self.season or "Season").strip(),
            f"Flight {self.flight_number}" if self.flight_number is not None else "Flight",
        ]
        return "_".join(filename_safe(p) or "Report" for p in parts)

    @property
    def report_filename(self):
        return f"{self.report_basename}.pdf"

    def ensure_share_token(self):
        """
        Guarantee this flight has a public token. Rows that predate the column,
        or that were created by a path which skipped the ORM default, come back
        with share_token = NULL, and the farmer's link then points at /r/None.
        Minting one lazily here means a link is never handed out broken.
        Returns True when a new token was written (caller commits).
        """
        if (self.share_token or "").strip():
            return False
        self.share_token = secrets.token_urlsafe(16)
        return True

    @property
    def complete_findings(self):
        return [f for f in self.findings if f.is_complete]

    @property
    def incomplete_count(self):
        return sum(1 for f in self.findings if not f.is_complete)

    summary_edits = db.relationship("SummaryEdit", backref="flight", lazy=True,
                                    cascade="all, delete-orphan")

    @property
    def map_link(self):
        """
        Where the interactive map lives for this flight.

        A flight may carry its own project link, but most do not, and those
        follow the farm's. Reading it through here rather than copying the
        farm's value onto the flight means changing it on the farm reaches every
        flight that never had one of its own.
        """
        own = (self.dronedeploy_project_url or "").strip()
        if own:
            return own
        return (self.farm.dronedeploy_project_url or "").strip() if self.farm else ""

    @property
    def farmer_comment_count(self):
        """Findings the farmer has commented on. Internal; never in the report."""
        return sum(1 for f in self.findings if f.has_farmer_comment)

    @property
    def can_generate(self):
        """
        A report needs the map as well as the findings.

        The annotated map is what the farmer reads first and what the numbered
        findings refer back to, so a report without it is a list of pin numbers
        pointing at nothing.
        """
        return (len(self.findings) > 0
                and self.incomplete_count == 0
                and bool(self.map_image))

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
    # What the farmer said back about this finding, recorded by customer success
    # after they have spoken to them. Internal only: it never reaches the report,
    # because the report is the record of what was advised, and a farmer
    # disputing a finding is a note about that advice rather than part of it.
    farmer_comment = db.Column(db.Text, default="")
    farmer_comment_at = db.Column(db.DateTime)
    farmer_comment_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    farmer_comment_by = db.relationship("User", foreign_keys=[farmer_comment_by_id])
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_complete(self):
        return bool(self.category and self.observation.strip()
                    and self.likely_cause.strip() and self.recommendation.strip())

    @property
    def has_farmer_comment(self):
        return bool((self.farmer_comment or "").strip())


class SummaryEdit(db.Model):
    """
    An agronomist's wording for one pattern on the summary page.

    The summary is assembled from the findings, which is the right default but
    not always the right sentence: the agronomist knows the field and may want to
    say it differently before it reaches the farmer. A row here overrides the
    assembled text for one category on one flight; where there is no row, or the
    text is blank, the assembled version stands.

    Keyed by category rather than by position, so re-importing the export and
    renumbering the zones does not detach an edit from what it describes.
    """
    __tablename__ = "summary_edits"
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey("flights.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    category = db.Column(db.String(60), nullable=False)
    observation = db.Column(db.Text, default="")
    suggestion = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    __table_args__ = (db.UniqueConstraint("flight_id", "category", name="uq_summary_edit"),)


class SiteContent(db.Model):
    """
    One editable homepage value, keyed by name.

    A row exists only once an admin has saved that key; anything unsaved falls
    back to the default in homepage.py. Storing one row per key rather than a
    single JSON blob means a key added in a later release needs no migration —
    it simply has no row yet.
    """
    __tablename__ = "site_content"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])


def slugify(text):
    text = (text or "").strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_") or "Report"


def filename_safe(text):
    """
    Strip only what a filesystem or an HTTP header cannot carry, and keep the
    rest — including spaces, so `Kilimo Bora Farm` survives as written. The
    underscore is reserved as the separator between the report's three parts,
    so any underscore already inside a name becomes a space.
    """
    text = (text or "").strip()
    text = text.replace("_", " ")
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "", text)      # illegal on disk or in headers
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")
