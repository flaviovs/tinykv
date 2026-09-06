"""A lightweight Python SQLite key-value store built on sqlite3."""

import enum
import itertools
import logging
import math
import pickle
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\Z")


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


__version__ = "0.2.1"

_DEF_TABLE = "kv"
_NAN_PICKLE = pickle.dumps(float("nan"))
_MISSING = object()

logger = logging.getLogger(__name__)


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
    quoted_table = _quote_table_name(table)
    if_not_exists_sql = "IF NOT EXISTS " if if_not_exists else ""
    conn.execute(
        f"CREATE TABLE {if_not_exists_sql}{quoted_table} ("
        "k TEXT NOT NULL, "
        "t TINYINT NOT NULL CHECK (t BETWEEN 1 AND 7), "
        "v BLOB, "
        "PRIMARY KEY (k)"
        ") WITHOUT ROWID"
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

    def set(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Store a value in the database.

        Args:
            key: The key to store the value under.
            value: The value to store.

        Raises:
            TypeError: If key is not a string.
            ValueError: If key is an empty string or the database row is
                malformed.

        """
        _validate_key(key)
        assert self._conn
        dtype, data = self._serialize(value)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self._quoted_table} "
            "(k, t, v) "
            "VALUES (?, ?, ?)",
            (key, dtype, data),
        )

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
            f"SELECT t, v FROM {self._quoted_table} WHERE k = ?", (key,)
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
            ValueError: If any key is an empty string or a database row is
                malformed.

        """
        assert self._conn
        result: dict[str, Any] = {}
        key_iter = iter(keys)
        chunk_size = max(
            1,
            self._conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER),
        )
        while True:
            tkeys = tuple(itertools.islice(key_iter, chunk_size))
            if not tkeys:
                break
            for k in tkeys:
                _validate_key(k)
            rows = self._conn.execute(
                f"SELECT k, t, v FROM {self._quoted_table} WHERE "
                f"k IN ({', '.join(['?'] * len(tkeys))})",
                tkeys,
            )
            result.update(
                {r[0]: self._decode_row(r[1], r[2]) for r in rows.fetchall()}
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
            f"SELECT k, t, v FROM {self._quoted_table} WHERE k GLOB ?",
            (glob_key,),
        )
        return {r[0]: self._decode_row(r[1], r[2]) for r in rows.fetchall()}

    def set_many(self, kvdict: Mapping[str, Any]) -> None:
        """Set many values at once.

        Args:
            kvdict: A mapping of keys to values.

        Raises:
            TypeError: If any key is not a string.
            ValueError: If any key is an empty string.

        """
        assert self._conn
        item_iter = iter(kvdict.items())
        first_chunk = list(itertools.islice(item_iter, 1))
        if not first_chunk:
            return
        chunk_size = max(
            1,
            self._conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) // 3,
        )
        self._conn.execute("SAVEPOINT tinykv_batch")
        try:
            chunk = first_chunk
            while chunk:
                params: list[Any] = []
                for k, value in chunk:
                    _validate_key(k)
                    params.extend((k, *self._serialize(value)))
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {self._quoted_table} (k, t, v) "
                    f"VALUES {', '.join(['(?, ?, ?)'] * len(chunk))}",
                    params,
                )
                chunk = list(itertools.islice(item_iter, chunk_size))
        except Exception:
            self._conn.execute("ROLLBACK TO tinykv_batch")
            self._conn.execute("RELEASE tinykv_batch")
            raise
        self._conn.execute("RELEASE tinykv_batch")

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
            f"DELETE FROM {self._quoted_table} WHERE k = ?", (key,)
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
        self._conn.execute("SAVEPOINT tinykv_batch")
        try:
            tkeys = first_chunk
            while tkeys:
                for k in tkeys:
                    _validate_key(k)
                self._conn.execute(
                    f"DELETE FROM {self._quoted_table} WHERE "
                    f"k IN ({', '.join(['?'] * len(tkeys))})",
                    tkeys,
                )
                tkeys = tuple(itertools.islice(key_iter, chunk_size))
        except Exception:
            self._conn.execute("ROLLBACK TO tinykv_batch")
            self._conn.execute("RELEASE tinykv_batch")
            raise
        self._conn.execute("RELEASE tinykv_batch")
