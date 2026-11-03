"""A lightweight Python SQLite key-value store built on sqlite3."""

import enum
import itertools
import logging
import math
import pickle
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from types import EllipsisType, TracebackType
from typing import Any

__version__ = "0.4.0"

_DEF_TABLE = "kv"
_SCHEMA_VERSION_KEY = "__tinykv_schema_version"
_SCHEMA_VERSION = 2
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_NAN_PICKLE = pickle.dumps(float("nan"))
_MISSING = object()
_SAVEPOINT_IDS = itertools.count()
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\Z")
logger = logging.getLogger(__name__)


def _split_table_name(table: str) -> tuple[str | None, str]:
    if not isinstance(table, str):
        raise TypeError(
            f"table name must be a string, got {type(table).__name__}"
        )
    parts = table.split(".")
    if len(parts) == 1:
        schema, name = None, parts[0]
    elif len(parts) == 2:
        schema, name = parts
    else:
        schema, name = None, ""
    if not _TABLE_NAME_RE.fullmatch(name) or (
        schema is not None and not _TABLE_NAME_RE.fullmatch(schema)
    ):
        raise ValueError(
            f"Invalid table name {table!r}: must match pattern "
            r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?"
        )
    return schema, name


def _validate_table_name(table: str) -> None:
    _split_table_name(table)


def _quote_table_name(table: str) -> str:
    schema, name = _split_table_name(table)
    quoted_name = f'"{name}"'
    if schema is None:
        return quoted_name
    return f'"{schema}".{quoted_name}'


def _validate_key(key: str) -> None:
    if not isinstance(key, str):
        raise TypeError(f"key must be a string, got {type(key).__name__}")
    if not key:
        raise ValueError("key must not be empty")
    if key.startswith("__tinykv_"):
        raise ValueError(
            "key must not start with the reserved prefix '__tinykv_'"
        )


def _validate_int(value: Any, name: str) -> None:  # noqa: ANN401
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be an integer, got {type(value).__name__}"
        )


def _ttl_expiration(ttl: int | None) -> int | None:
    if ttl is None:
        return None
    _validate_int(ttl, "ttl")
    if ttl < 0:
        raise ValueError("ttl must be nonnegative")
    expires_at = int(time.time()) + ttl
    if expires_at > _MAX_SQLITE_INTEGER:
        raise ValueError("ttl is too large for a SQLite INTEGER timestamp")
    return expires_at


class _Savepoint:
    """Manage an atomic operation using a uniquely named savepoint."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._name = f"__tinykv_{next(_SAVEPOINT_IDS)}"

    def __enter__(self) -> "_Savepoint":
        self._conn.execute(f"SAVEPOINT {self._name}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._conn.execute(f"ROLLBACK TO {self._name}")
        self._conn.execute(f"RELEASE {self._name}")


def _migrate_schema(
    conn: sqlite3.Connection,
    table: str,
    quoted_table: str,
) -> None:
    """Check a table's schema version and migrate it when necessary."""
    version_row = conn.execute(
        f"SELECT v FROM {quoted_table} WHERE k = ?",
        (_SCHEMA_VERSION_KEY,),
    ).fetchone()
    schema_version = (
        1 if version_row is None else int(bytes(version_row[0]).decode())
    )
    if schema_version > _SCHEMA_VERSION:
        raise RuntimeError(
            f"Table {table!r} has unsupported schema version {schema_version}"
        )
    if schema_version < _SCHEMA_VERSION:
        from ._migrations import migrate_schema  # noqa: PLC0415

        with _Savepoint(conn):
            migrate_schema(conn, table, quoted_table, schema_version)


def create_schema(
    conn: sqlite3.Connection,
    table: str = _DEF_TABLE,
    if_not_exists: bool = False,
) -> None:
    """Create a database table for use with tinykv.

    Args:
        conn: The SQLite3 connection object.
        table: The table name (default: 'kv').
        if_not_exists: If true, include SQLite's `IF NOT EXISTS` clause so
            repeated schema creation is a no-op.

    """
    schema, name = _split_table_name(table)
    quoted_table = _quote_table_name(table)
    if_not_exists_sql = "IF NOT EXISTS " if if_not_exists else ""
    catalog = (
        "sqlite_master" if schema is None else f'"{schema}".sqlite_master'
    )
    existed = (
        conn.execute(
            f"SELECT 1 FROM {catalog} WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )
    with _Savepoint(conn):
        conn.execute(
            f"CREATE TABLE {if_not_exists_sql}{quoted_table} ("
            "k TEXT NOT NULL, "
            "t TINYINT NOT NULL CHECK (t BETWEEN 1 AND 7), "
            "v BLOB, "
            "e INT CHECK (e > 0), "
            "PRIMARY KEY (k)"
            ") WITHOUT ROWID"
        )
        if not existed:
            conn.execute(
                f"INSERT INTO {quoted_table} (k, t, v, e) "
                "VALUES (?, ?, ?, NULL)",
                (
                    _SCHEMA_VERSION_KEY,
                    _DType.LONG,
                    str(_SCHEMA_VERSION).encode(),
                ),
            )


class _DType(enum.IntEnum):
    NONE = 1
    STRING = 2
    BYTES = 3
    BOOL = 4
    NUMBER = 5
    PICKLE = 6
    LONG = 7


class TinyKV:
    """The SQLite3 key-value object.

    Basic usage:

    >>> import sqlite3
    >>>
    >>> conn = sqlite3.connect(':memory:')
    >>>
    >>> create_schema(conn)
    >>>
    >>> kv = TinyKV(conn, allow_pickle=True)

    Args:
        conn: The SQLite3 connection object.
        table: The table name (default: 'kv').
        allow_pickle: If true, unsupported values are serialized with pickle.
            Pickle-based storage and deserialization are disabled by default.
            Enable this explicitly only when database contents are trusted.

    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        table: str = _DEF_TABLE,
        allow_pickle: bool = False,
    ) -> None:
        """Initialize the key-value object.

        Args:
            conn: The SQLite3 connection object.
            table: The table name (default: 'kv').
            allow_pickle: If true, fallback to pickle for unsupported value
                types and unpickle stored pickle rows. Pickle-based storage and
                deserialization are disabled by default. If false, both
                operations raise ValueError.

        """
        if not isinstance(allow_pickle, bool):
            raise TypeError(
                f"allow_pickle must be a boolean, got "
                f"{type(allow_pickle).__name__}"
            )
        schema, name = _split_table_name(table)
        quoted_table = _quote_table_name(table)
        catalog = (
            "sqlite_master" if schema is None else f'"{schema}".sqlite_master'
        )
        cur = conn.execute(
            f"SELECT name FROM {catalog} WHERE type = 'table' AND name = ?",
            (name,),
        )
        if not cur.fetchone():
            raise RuntimeError(f"Table {table!r} not found in the database")

        self._conn = conn
        self._table = table
        self._quoted_table = quoted_table
        self._allow_pickle = allow_pickle
        _migrate_schema(conn, table, quoted_table)

    @property
    def conn(self) -> sqlite3.Connection:
        """The SQLite3 connection being used by this tinykv object.

        Note: this is a read-only attribute.

        """
        return self._conn

    def _serialize(
        self,
        data: Any,  # noqa: ANN401
    ) -> tuple[_DType, float | bytes | None]:
        if data is None:
            return (_DType.NONE, None)

        if isinstance(data, str):
            return (_DType.STRING, data.encode("utf-8"))

        if isinstance(data, bytes):
            return (_DType.BYTES, data)

        if isinstance(data, bool):
            return (_DType.BOOL, int(data))

        if isinstance(data, int):
            return (_DType.LONG, str(data).encode("utf-8"))

        if isinstance(data, float):
            if math.isnan(data):
                if self._allow_pickle:
                    return (_DType.PICKLE, pickle.dumps(data))
                return (_DType.NUMBER, b"nan")
            return (_DType.NUMBER, data)

        if self._allow_pickle:
            return (_DType.PICKLE, pickle.dumps(data))

        raise ValueError(
            "Cannot store value type without pickle support; "
            "initialize TinyKV with allow_pickle=True to enable "
            "legacy pickled values"
        )

    def _unserialize(self, dtype: _DType, data: Any) -> Any:  # noqa: ANN401
        if dtype == _DType.NONE:
            if data is not None:
                raise ValueError("Malformed data for type NONE")
            return None

        if dtype == _DType.STRING:
            if not isinstance(data, bytes):
                raise ValueError("Malformed data for type STRING")
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Malformed data for type STRING") from exc

        if dtype == _DType.BYTES:
            if not isinstance(data, bytes):
                raise ValueError("Malformed data for type BYTES")
            return data

        if dtype == _DType.BOOL:
            if not isinstance(data, int) or data not in (0, 1):
                raise ValueError("Malformed data for type BOOL")
            return bool(data)

        if dtype == _DType.NUMBER:
            if isinstance(data, bytes):
                if data != b"nan":
                    raise ValueError("Malformed data for type NUMBER")
            elif not isinstance(data, (int, float)):
                raise ValueError("Malformed data for type NUMBER")
            try:
                return float(data)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Malformed data for type NUMBER") from exc

        if dtype == _DType.LONG:
            if not isinstance(data, bytes):
                raise ValueError("Malformed data for type LONG")
            try:
                return int(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Malformed data for type LONG") from exc

        if dtype == _DType.PICKLE:
            if not self._allow_pickle:
                if data == _NAN_PICKLE:
                    return float("nan")
                raise ValueError(
                    "Cannot deserialize pickled value with allow_pickle=False"
                )
            if not isinstance(data, bytes):
                raise ValueError("Malformed data for type PICKLE")
            try:
                return pickle.loads(data)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise ValueError("Malformed data for type PICKLE") from exc

        raise ValueError(f"Unsupported data type {dtype}")

    def _decode_row(self, dtype: Any, data: Any) -> Any:  # noqa: ANN401
        try:
            row_type = _DType(dtype)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported data type {dtype}") from exc
        return self._unserialize(row_type, data)

    def set(
        self,
        key: str,
        value: Any,  # noqa: ANN401
        ttl: int | None = None,
    ) -> None:
        """Store a value in the database.

        Args:
            key: The key to store the value under.
            value: The value to store.
            ttl: Optional nonnegative lifetime in seconds. ``None`` keeps the
                key indefinitely.

        Raises:
            TypeError: If key is not a string.
            ValueError: If key is empty or starts with the reserved prefix, or
                the TTL is negative or exceeds SQLite's integer range.

        """
        _validate_key(key)
        expires_at = _ttl_expiration(ttl)
        self._set(key, value, expires_at)

    def set_ttl(self, key: str, ttl: int | None) -> None:
        """Set or clear the expiration for an existing key.

        ``ttl`` is a nonnegative lifetime in seconds. Passing ``None`` clears
        the expiration. Missing and expired keys raise ``KeyError``.

        Args:
            key: The key whose expiration to update.
            ttl: A nonnegative lifetime in seconds, or ``None`` to clear it.

        Raises:
            KeyError: If the key is missing or expired.
            TypeError: If the key or TTL has an invalid type.
            ValueError: If the key is empty or reserved, or TTL is negative or
                exceeds SQLite's integer range.

        """
        _validate_key(key)
        expires_at = _ttl_expiration(ttl)
        assert self._conn
        cursor = self._conn.execute(
            f"UPDATE {self._quoted_table} SET e = ? WHERE k = ? "
            "AND (e IS NULL OR e > CAST(strftime('%s', 'now') AS INT))",
            (expires_at, key),
        )
        if cursor.rowcount == 0:
            raise KeyError(key)

    def _set(
        self,
        key: str,
        value: Any,  # noqa: ANN401
        expires_at: int | None = None,
    ) -> None:
        """Store a value for a key that has already been validated."""
        assert self._conn
        dtype, data = self._serialize(value)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self._quoted_table} "
            "(k, t, v, e) "
            "VALUES (?, ?, ?, ?)",
            (key, dtype, data, expires_at),
        )

    def add(
        self,
        key: str,
        n: int,
        *,
        create_ttl: int | None = None,
        update_ttl: int | EllipsisType | None = Ellipsis,
    ) -> int:
        """Add an integer amount to the value stored under ``key``.

        If the key does not exist, it is treated as having value zero.
        Existing values must be integers; booleans and other types are not
        accepted. The operation is atomic and returns the new value.

        Args:
            key: The key to update.
            n: A signed integer amount to add.
            create_ttl: TTL in seconds for a key created by this operation.
                ``None`` creates a key without expiration.
            update_ttl: TTL in seconds to assign when updating an existing
                key. If omitted, its current TTL is preserved; ``None`` clears
                its expiration.

        Returns:
            The updated integer value.

        Raises:
            TypeError: If ``key``, ``n``, or either TTL has an invalid type,
                or if the existing value is not an integer.
            ValueError: If ``key`` is empty, starts with the reserved prefix,
                a TTL is negative or exceeds SQLite's integer range, or the
                stored row is malformed.

        """
        _validate_key(key)
        _validate_int(n, "n")
        create_expires_at = _ttl_expiration(create_ttl)
        update_expires_at = (
            Ellipsis if update_ttl is Ellipsis else _ttl_expiration(update_ttl)
        )
        return self._add(key, n, create_expires_at, update_expires_at)

    def _add(
        self,
        key: str,
        n: int,
        create_expires_at: int | None,
        update_expires_at: int | EllipsisType | None,
    ) -> int:
        """Add to a key whose name and amount have been validated."""
        assert self._conn
        with _Savepoint(self._conn):
            cur = self._conn.execute(
                f"SELECT t, v, e FROM {self._quoted_table} WHERE k = ? "
                "AND (e IS NULL OR e > CAST(strftime('%s', 'now') AS INT))",
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                value = 0
                expires_at = create_expires_at
            else:
                value = self._decode_row(row[0], row[1])
                _validate_int(value, f"value for key {key!r}")
                if update_expires_at is Ellipsis:
                    expires_at = row[2]
                else:
                    expires_at = update_expires_at

            result = value + n
            self._set(key, result, expires_at)
        return result

    def inc(
        self,
        key: str,
        n: int = 1,
        *,
        create_ttl: int | None = None,
        update_ttl: int | EllipsisType | None = Ellipsis,
    ) -> int:
        """Increment the integer value stored under ``key`` by ``n``.

        Args:
            key: The key to update.
            n: A nonnegative integer amount to add.
            create_ttl: TTL in seconds for a key created by this operation.
            update_ttl: TTL in seconds for an existing key. If omitted, its
                current TTL is preserved; ``None`` clears its expiration.

        Returns:
            The updated integer value.

        Raises:
            TypeError: If ``key``, ``n``, or either TTL has an invalid type,
                or if the existing value is not an integer.
            ValueError: If ``key`` is empty, ``n`` is negative, a TTL is
                negative or exceeds SQLite's integer range, or the stored row
                is malformed.

        """
        _validate_int(n, "n")
        if n < 0:
            raise ValueError("n must be nonnegative")
        return self.add(key, n, create_ttl=create_ttl, update_ttl=update_ttl)

    def dec(
        self,
        key: str,
        n: int = 1,
        *,
        create_ttl: int | None = None,
        update_ttl: int | EllipsisType | None = Ellipsis,
    ) -> int:
        """Decrement the integer value stored under ``key`` by ``n``.

        Args:
            key: The key to update.
            n: A nonnegative integer amount to subtract.
            create_ttl: TTL in seconds for a key created by this operation.
            update_ttl: TTL in seconds for an existing key. If omitted, its
                current TTL is preserved; ``None`` clears its expiration.

        Returns:
            The updated integer value.

        Raises:
            TypeError: If ``key``, ``n``, or either TTL has an invalid type,
                or if the existing value is not an integer.
            ValueError: If ``key`` is empty, ``n`` is negative, a TTL is
                negative or exceeds SQLite's integer range, or the stored row
                is malformed.

        """
        _validate_int(n, "n")
        if n < 0:
            raise ValueError("n must be nonnegative")
        return self.add(key, -n, create_ttl=create_ttl, update_ttl=update_ttl)

    def get(
        self,
        key: str,
        default: Any = _MISSING,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Get a value from the database.

        Will raise `KeyError` if the key is not found, unless
        `default` is provided, in which case its value is returned.

        Example:
            >>> conn = sqlite3.connect(':memory:')
            >>>
            >>> create_schema(conn)
            >>> kv = TinyKV(conn, allow_pickle=True)
            >>>
            >>> kv.set('foo', 'bar')
            >>> kv.get('foo')
            'bar'

        Args:
            key: The key.
            default: The value to return, if the key does not exist.

        Returns:
            The value associated with the key.

        Raises:
            KeyError: if the key does not exist and no default is
                provided.
            TypeError: If key is not a string.
            ValueError: If key is an empty string.

        """
        _validate_key(key)
        assert self._conn
        cur = self._conn.execute(
            f"SELECT t, v FROM {self._quoted_table} WHERE k = ? "
            "AND (e IS NULL OR e > CAST(strftime('%s', 'now') AS INT))",
            (key,),
        )
        row = cur.fetchone()
        if not row:
            if default is not _MISSING:
                return default
            raise KeyError(key)
        return self._decode_row(row[0], row[1])

    def get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        """Get many values from the database.

        Args:
            keys: An iterable of keys to return.

        Returns:
            A dict where with only the keys found on the database, and their
            respective values.

        Raises:
            TypeError: If any key is not a string.
            ValueError: If any key is empty or starts with the reserved
                prefix, or a database row is malformed.

        """
        assert self._conn
        result: dict[str, Any] = {}
        key_iter = iter(keys)
        chunk_size = max(
            1,
            self._conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER),
        )
        with _Savepoint(self._conn):
            while True:
                tkeys = tuple(itertools.islice(key_iter, chunk_size))
                if not tkeys:
                    break
                for k in tkeys:
                    _validate_key(k)
                rows = self._conn.execute(
                    f"SELECT k, t, v FROM {self._quoted_table} WHERE "
                    f"k IN ({', '.join(['?'] * len(tkeys))}) "
                    "AND (e IS NULL OR e > "
                    "CAST(strftime('%s', 'now') AS INT))",
                    tkeys,
                )
                result.update(
                    {
                        r[0]: self._decode_row(r[1], r[2])
                        for r in rows.fetchall()
                    }
                )
        return result

    def get_glob(self, glob_key: str) -> dict[str, Any]:
        """Get many values using a glob pattern.

        Similar to `get_many()`, but using a glob pattern.

        Args:
            glob_key: A shell-like wildcard for matching keys.

        Returns:
            A dict where keys are the keys matching `glob_key` found
            on the database, and their respective values.

        Raises:
            TypeError: If glob_key is not a string.
            ValueError: If glob_key is an empty string or a database row is
                malformed.

        """
        _validate_key(glob_key)
        assert self._conn
        rows = self._conn.execute(
            f"SELECT k, t, v FROM {self._quoted_table} WHERE k GLOB ? "
            "AND (e IS NULL OR e > CAST(strftime('%s', 'now') AS INT))",
            (glob_key,),
        )
        return {
            r[0]: self._decode_row(r[1], r[2])
            for r in rows.fetchall()
            if not r[0].startswith("__tinykv_")
        }

    def set_many(
        self,
        kvdict: Mapping[str, Any],
        ttl: int | None = None,
    ) -> None:
        """Set many values at once.

        Args:
            kvdict: A mapping of keys to values.
            ttl: Optional nonnegative lifetime in seconds for each key.
                ``None`` stores keys without expiration.

        Raises:
            TypeError: If any key is not a string.
            ValueError: If any key is empty or starts with the reserved prefix,
                or ``ttl`` is negative or exceeds SQLite's integer range.

        """
        expires_at = _ttl_expiration(ttl)
        assert self._conn
        item_iter = iter(kvdict.items())
        first_chunk = list(itertools.islice(item_iter, 1))
        if not first_chunk:
            return
        expiration_sql = "NULL" if expires_at is None else str(expires_at)
        chunk_size = max(
            1,
            self._conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) // 3,
        )
        with _Savepoint(self._conn):
            chunk = first_chunk
            while chunk:
                params: list[Any] = []
                for k, value in chunk:
                    _validate_key(k)
                    dtype, data = self._serialize(value)
                    params.extend((k, dtype, data))
                values_sql = ", ".join(
                    [f"(?, ?, ?, {expiration_sql})"] * len(chunk)
                )
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {self._quoted_table} "
                    f"(k, t, v, e) VALUES {values_sql}",
                    params,
                )
                chunk = list(itertools.islice(item_iter, chunk_size))

    def remove(self, key: str) -> None:
        """Remove a key from the database.

        Args:
            key: The key to remove.

        Raises:
            KeyError: If the key is not found.
            TypeError: If key is not a string.
            ValueError: If key is an empty string.

        """
        _validate_key(key)
        assert self._conn
        cur = self._conn.execute(
            f"DELETE FROM {self._quoted_table} WHERE k = ? "
            "AND (e IS NULL OR e > CAST(strftime('%s', 'now') AS INT))",
            (key,),
        )
        if cur.rowcount == 0:
            raise KeyError(key)

    def remove_many(self, keys: Iterable[str]) -> None:
        """Remove many keys from the database at once.

        Nonexistent keys are silently ignored.

        Args:
            keys: Iterable of keys to remove.

        Raises:
            TypeError: If any key is not a string.
            ValueError: If any key is an empty string.
        """
        assert self._conn
        key_iter = iter(keys)
        first_chunk = tuple(itertools.islice(key_iter, 1))
        if not first_chunk:
            return
        chunk_size = max(
            1,
            self._conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER),
        )
        with _Savepoint(self._conn):
            tkeys = first_chunk
            while tkeys:
                for k in tkeys:
                    _validate_key(k)
                self._conn.execute(
                    f"DELETE FROM {self._quoted_table} WHERE "
                    f"k IN ({', '.join(['?'] * len(tkeys))}) "
                    "AND (e IS NULL OR e > "
                    "CAST(strftime('%s', 'now') AS INT))",
                    tkeys,
                )
                tkeys = tuple(itertools.islice(key_iter, chunk_size))

    def gc(self) -> int:
        """Delete expired keys and return the number of rows removed."""
        assert self._conn
        with _Savepoint(self._conn):
            cursor = self._conn.execute(
                f"DELETE FROM {self._quoted_table} WHERE e IS NOT NULL "
                "AND e <= ?",
                (int(time.time()),),
            )
        return cursor.rowcount
