"""Apply sql/migrations/*.sql to the database at DATABASE_URL (same migrations the app
applies automatically on startup - this is just a convenience for running it manually)."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import apply_migrations

load_dotenv()

if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applied = apply_migrations(conn)
    print(f"applied {len(applied)} migration(s): {applied}" if applied else "schema already up to date")
