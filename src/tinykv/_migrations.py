"""Database schema migrations for TinyKV.

This module is imported only when opening a database that needs migration.
Each migration advances exactly one schema version.
"""

import sqlite3
from collections.abc import Callable

_SCHEMA_VERSION_KEY = "__tinykv_schema_version"


def migrate_v1_to_v2(
    conn: sqlite3.Connection,
    table: str,
    quoted_table: str,
) -> None:
    """Add the expiration column and mark the table as schema v2."""
    schema, name = (None, table)
    if "." in table:
        schema, name = table.split(".", 1)
    pragma = "table_info" if schema is None else f'"{schema}".table_info'
    columns = {
        row[1] for row in conn.execute(f'PRAGMA {pragma}("{name}")').fetchall()
    }
    if "e" not in columns:
        conn.execute(
            f"ALTER TABLE {quoted_table} ADD COLUMN e INT CHECK (e > 0)"
        )
    conn.execute(
        f"INSERT OR REPLACE INTO {quoted_table} (k, t, v, e) "
        "VALUES (?, ?, ?, NULL)",
        (_SCHEMA_VERSION_KEY, 7, b"2"),
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection, str, str], None]] = {
    1: migrate_v1_to_v2,
}


def migrate_schema(
    conn: sqlite3.Connection,
    table: str,
    quoted_table: str,
    current_version: int,
) -> None:
    """Apply each required migration in ascending version order."""
    version = current_version
    while version in _MIGRATIONS:
        _MIGRATIONS[version](conn, table, quoted_table)
        version += 1
