"""
Additive schema reconciliation for SHAMBA Tracker.

The app ships without Alembic, but it is already deployed against a live
database. When a release adds a column, `db.create_all()` will not add it to a
table that already exists, and every query against that table then fails.

`ensure_schema()` closes that gap: it reads the columns the database actually
has and issues a plain `ALTER TABLE ... ADD COLUMN` for each one the model
declares but the database is missing. It only ever adds; it never drops,
renames or retypes, so running it against an up-to-date database is a no-op and
running it twice is safe.

Limits worth knowing:
  * Only added columns are reconciled. A changed type or a new table constraint
    still needs a real migration.
  * SQLite cannot add a column that is both NOT NULL and without a default, so
    every column added here carries a default.
"""
from sqlalchemy import inspect, text

# table -> column -> DDL fragment used when the column is missing.
# Types are written in a dialect-neutral way that both SQLite and PostgreSQL
# accept — note BOOLEAN DEFAULT FALSE rather than DEFAULT 0, which PostgreSQL
# rejects as an integer default on a boolean column — and every entry has a DEFAULT so existing rows get a sane value.
ADDITIONS = {
    "users": {
        "phone":          "VARCHAR(60) DEFAULT ''",
        "job_title":      "VARCHAR(120) DEFAULT ''",
        "location":       "VARCHAR(160) DEFAULT ''",
        "bio":            "TEXT DEFAULT ''",
        "status":         "VARCHAR(20) DEFAULT 'approved'",
        "requested_role": "VARCHAR(30) DEFAULT 'agronomist'",
        "approved_at":    "TIMESTAMP NULL",
        "approved_by_id": "INTEGER NULL",
        "decision_note":  "TEXT DEFAULT ''",
        "last_login_at":  "TIMESTAMP NULL",
    },
    "findings": {
        # Customer success records what the farmer said back about a finding,
        # usually when the farmer's own knowledge of the field disagrees with
        # what was reported. Internal only; it never reaches the report.
        "farmer_comment":       "TEXT DEFAULT ''",
        "farmer_comment_at":    "TIMESTAMP NULL",
        "farmer_comment_by_id": "INTEGER NULL",
    },
    "flights": {
        "delivery_method":  "VARCHAR(40) DEFAULT ''",
        # share_token drives the farmer's report link. A database that predates
        # it fails every Flight query without this entry, and a row that has the
        # column but no value hands out /r/None — see backfill_share_tokens().
        "share_token":      "VARCHAR(48) NULL",
        "sent_email":       "BOOLEAN DEFAULT FALSE",
        "sent_whatsapp":    "BOOLEAN DEFAULT FALSE",
        "sent_at":          "TIMESTAMP NULL",
        "acknowledged":     "BOOLEAN DEFAULT FALSE",
        "acknowledged_at":  "TIMESTAMP NULL",
        "report_pdf":       "VARCHAR(300) DEFAULT ''",
        "agronomist_note":  "TEXT DEFAULT ''",
    },
}

# Columns whose stored value must be backfilled once, after the column exists.
# Anyone already in the database predates the approval queue, so they are
# grandfathered in as approved rather than being locked out by the new gate.
BACKFILLS = [
    ("users", "UPDATE users SET status='approved' WHERE status IS NULL OR status=''"),
    ("users", "UPDATE users SET requested_role=role WHERE requested_role IS NULL OR requested_role=''"),
]


def ensure_schema(db):
    """Add any model column the live database is missing. Returns what it did."""
    added = []
    try:
        insp = inspect(db.engine)
        existing_tables = set(insp.get_table_names())
    except Exception as exc:                       # database not reachable yet
        print(f"[schema] could not inspect database: {exc}")
        return added

    for table, columns in ADDITIONS.items():
        if table not in existing_tables:
            continue                               # create_all() will build it in full
        have = {c["name"] for c in insp.get_columns(table)}
        for column, ddl in columns.items():
            if column in have:
                continue
            stmt = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(stmt))
                added.append(f"{table}.{column}")
            except Exception as exc:
                # Another worker almost certainly won the race and added it.
                print(f"[schema] skipped {table}.{column}: {exc}")

    if added:
        for table, stmt in BACKFILLS:
            if table not in existing_tables:
                continue
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                print(f"[schema] backfill skipped: {exc}")
        print(f"[schema] added columns: {', '.join(added)}")

    if "flights" in existing_tables:
        backfill_share_tokens(db)
        if "farms" in existing_tables:
            release_copied_project_urls(db)
    if "findings" in existing_tables:
        migrate_categories(db)
        reclassify_colour_guesses(db)
    return added


def migrate_categories(db):
    """
    Move findings off the category names used before the review page and the
    report legend were merged onto one list.

    Without this, a flight recorded earlier still reads "Nutrient / Vigor" on the
    review page while its report groups it under "Soil Fertility / Nutrition" —
    the same mismatch the merge was meant to remove. Idempotent: a database
    already using the current names has nothing to update.
    """
    from parsing import LEGACY_CATEGORIES
    moved = 0
    try:
        with db.engine.begin() as conn:
            for old_name, new_name in LEGACY_CATEGORIES.items():
                result = conn.execute(
                    text("UPDATE findings SET category=:new WHERE category=:old"),
                    {"new": new_name, "old": old_name})
                moved += result.rowcount or 0
        if moved:
            print(f"[schema] moved {moved} finding(s) onto the current category names")
    except Exception as exc:
        print(f"[schema] category migration skipped: {exc}")


# What the old colour-to-category map produced. A finding still carrying one of
# these, on a pin of the matching colour, was filed by the colour rather than by
# anything the agronomist wrote.
COLOUR_GUESSES = {
    "New growth": "Soil Fertility / Nutrition",
    "Healthy": "Soil Fertility / Nutrition",
    "Monitor": "Needs Investigation",
    "Needs testing": "Pest / Disease",
    "Pending review": "Needs Investigation",
}


def reclassify_colour_guesses(db):
    """
    Re-derive the category for findings that were filed by pin colour.

    Colour records how urgent an area is, not what kind of problem it is, so a
    category derived from it carries no information — on a flight where every
    pin was red, all of them arrived as "Pest / Disease" whatever the notes
    said. Those are re-read from the agronomist's own text.

    Only findings whose stored category still equals the guess their colour
    would have produced are touched, so a category an agronomist chose by hand
    is left exactly as they set it.
    """
    from aggregation import classify_text
    moved = 0
    try:
        with db.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, colour_meaning, category, observation, likely_cause, "
                "recommendation FROM findings")).fetchall()
            for fid, meaning, category, obs, cause, rec in rows:
                if COLOUR_GUESSES.get((meaning or "").strip()) != (category or "").strip():
                    continue
                derived = classify_text(obs or "", cause or "", rec or "")
                if derived and derived != category:
                    conn.execute(text("UPDATE findings SET category=:c WHERE id=:i"),
                                 {"c": derived, "i": fid})
                    moved += 1
        if moved:
            print(f"[schema] re-read {moved} finding(s) whose category came from the pin colour")
    except Exception as exc:
        print(f"[schema] category re-read skipped: {exc}")


def release_copied_project_urls(db):
    """
    Let a flight follow its farm's DroneDeploy link instead of holding a copy.

    A flight used to be given a copy of the farm's project URL when it was
    created. A copy stops tracking what it was copied from, so correcting the
    link on the farm left every existing report pointing at the old one.

    Only rows whose copy is *character for character* the farm's current link
    are cleared: those resolve to the same page either way, so nothing is lost
    and the farm becomes the one place the link lives. A flight pointing
    somewhere else is left alone — releasing it is the farm edit's job, where a
    person has actually asked for the change. Idempotent, so it is a no-op on a
    database that has already been through it.
    """
    cleared = 0
    try:
        with db.engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE flights SET dronedeploy_project_url='' "
                "WHERE dronedeploy_project_url IS NOT NULL "
                "  AND dronedeploy_project_url <> '' "
                "  AND dronedeploy_project_url = ("
                "        SELECT farms.dronedeploy_project_url FROM farms "
                "        WHERE farms.id = flights.farm_id)"))
            cleared = result.rowcount or 0
        if cleared:
            print(f"[schema] {cleared} flight(s) now follow their farm's DroneDeploy link")
    except Exception as exc:
        print(f"[schema] project-link release skipped: {exc}")


def backfill_share_tokens(db):
    """
    Give every flight a public token.

    Each token must be unique, so this cannot be a single UPDATE — it reads the
    rows that are missing one and writes a fresh token per row. It runs on every
    boot and is a no-op once the table is clean, which is what makes the
    farmer's report link safe to hand out: a flight created before the column
    existed would otherwise resolve to /r/None and 404 in the farmer's hand.
    """
    import secrets
    try:
        with db.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id FROM flights WHERE share_token IS NULL OR share_token = ''"
            )).fetchall()
            for (flight_id,) in rows:
                for _ in range(5):                 # retry on the (vanishing) collision
                    token = secrets.token_urlsafe(16)
                    try:
                        conn.execute(text("UPDATE flights SET share_token=:t WHERE id=:i"),
                                     {"t": token, "i": flight_id})
                        break
                    except Exception:
                        continue
        if rows:
            print(f"[schema] issued share tokens for {len(rows)} flight(s)")
    except Exception as exc:
        print(f"[schema] share-token backfill skipped: {exc}")
