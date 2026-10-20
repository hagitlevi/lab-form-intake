"""Apply sql/schema.sql to the database at DATABASE_URL."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    print("schema applied")
