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
# accept, and every entry has a DEFAULT so existing rows get a sane value.
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
    "flights": {
        "delivery_method": "VARCHAR(40) DEFAULT ''",
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
    return added
