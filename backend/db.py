from psycopg_pool import ConnectionPool

from .config import DATABASE_URL

pool = ConnectionPool(DATABASE_URL, open=False)
