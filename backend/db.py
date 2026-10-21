from pathlib import Path

from psycopg_pool import ConnectionPool

from .config import DATABASE_URL

pool = ConnectionPool(DATABASE_URL, open=False)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "sql" / "migrations"


def apply_migrations(conn) -> list[str]:
    """Plain SQL migrations: every sql/migrations/*.sql file not yet recorded in
    schema_migrations is applied, in filename order, each in its own transaction.
    Returns the filenames actually applied (empty if the schema was already current)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()}

    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
        newly_applied.append(path.name)
    conn.commit()
    return newly_applied


def mark_interrupted_batches(conn) -> int:
    """On startup: any batch still 'processing' means the server died mid-run (crash, or a
    dev-server --reload restart) - the error is never a caught Python exception, so this
    is the only place it gets a message at all."""
    cur = conn.execute(
        """
        UPDATE batches SET status = 'interrupted',
            error = 'העיבוד נקטע עקב הפעלה מחדש של השרת לפני שהסתיים'
        WHERE status = 'processing'
        """
    )
    conn.commit()
    return cur.rowcount
