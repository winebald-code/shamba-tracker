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
    return added


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
