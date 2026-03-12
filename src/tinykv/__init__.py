"""A tiny key-value store using SQLite3."""
import enum
import logging
import pickle
import sqlite3
import warnings

from typing import Any, Optional, Union, Iterable, Mapping, Tuple, Dict

__version__ = '0.1.2'

_DEF_TABLE = 'kv'
_ALLOW_PICKLE_DEFAULT = object()

logger = logging.getLogger(__name__)


def create_schema(conn: sqlite3.Connection, table: str = _DEF_TABLE) -> None:
    """Create a database table for use with tinykv.

    Args:
        conn: The SQLite3 connection object.
        table: The table name (default: 'kv').

    """
    conn.execute(f'CREATE TABLE {table} ('
                 'k TEXT NOT NULL COLLATE NOCASE, '
                 't TINYINT NOT NULL CHECK (t BETWEEN 1 AND 7), '
                 'v BLOB, '
                 'PRIMARY KEY (k)'
                 ')')


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
            If omitted, TinyKV currently behaves as true and emits a
            FutureWarning because the default is expected to change in a
            future release. Set to false to disable pickle-based
            storage/deserialization for safer handling of untrusted database
            content.

    """

    def __init__(self, conn: sqlite3.Connection, table: str = _DEF_TABLE,
                 allow_pickle: Union[bool, object] = _ALLOW_PICKLE_DEFAULT):
        """Initialize the key-value object.

        Args:
            conn: The SQLite3 connection object.
            table: The table name (default: 'kv').
            allow_pickle: If true, fallback to pickle for unsupported value
                types and unpickle stored pickle rows. If omitted, TinyKV
                currently behaves as true and emits a FutureWarning because the
                default is expected to change to false in a future release. If
                false, both operations raise ValueError.

        """
        cur = conn.execute('SELECT name FROM sqlite_master '
                           "WHERE type = 'table' AND name = ?",
                           (table,))
        if not cur.fetchone():
            raise RuntimeError(f'Table {table!r} not found in the database')

        self._conn = conn
        self._table = table
        if allow_pickle is _ALLOW_PICKLE_DEFAULT:
            warnings.warn('allow_pickle currently defaults to True but this '
                          'will change to False in a future release; pass '
                          'allow_pickle explicitly',
                          FutureWarning,
                          stacklevel=2)
            self._allow_pickle = True
        else:
            self._allow_pickle = bool(allow_pickle)

    @property
    def conn(self) -> sqlite3.Connection:
        """The SQLite3 connection being used by this tinykv object.

        Note: this is a read-only attribute.

        """
        return self._conn

    def _serialize(self, data: Any) -> Tuple[_DType,
                                             Optional[Union[float, bytes]]]:
        if data is None:
            return (_DType.NONE, None)

        if isinstance(data, str):
            return (_DType.STRING, data.encode('utf-8'))

        if isinstance(data, bytes):
            return (_DType.BYTES, data)

        if isinstance(data, bool):
            return (_DType.BOOL, int(data))

        if isinstance(data, int):
            return (_DType.LONG, str(data).encode('utf-8'))

        if isinstance(data, float):
            return (_DType.NUMBER, data)

        if self._allow_pickle:
            return (_DType.PICKLE, pickle.dumps(data))

        raise ValueError('Cannot store value type without pickle support; '
                         'initialize TinyKV with allow_pickle=True to enable '
                         'legacy pickled values')

    def _unserialize(self, dtype: _DType, data: bytes) -> Any:
        if dtype == _DType.NONE:
            return None

        if dtype == _DType.STRING:
            return data.decode('utf-8')

        if dtype == _DType.BYTES:
            return data

        if dtype == _DType.BOOL:
            return bool(data)

        if dtype == _DType.NUMBER:
            number = float(data)
            return int(number) if number.is_integer() else number

        if dtype == _DType.LONG:
            return int(data.decode('utf-8'))

        if dtype == _DType.PICKLE:
            if not self._allow_pickle:
                raise ValueError('Cannot deserialize pickled value with '
                                 'allow_pickle=False')
            return pickle.loads(data)

        raise ValueError('Unsupported data type {dtype}')

    def set(self, key: str, value: Any) -> None:
        """Store a value in the database."""
        assert self._conn
        dtype, data = self._serialize(value)
        self._conn.execute(f'INSERT OR REPLACE INTO {self._table} (k, t, v) '
                           'VALUES (?, ?, ?)',
                           (key, dtype, data))

    def get(self, key: str, default: Any = ...) -> Any:
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

        """
        assert self._conn
        cur = self._conn.execute(f'SELECT t, v FROM {self._table} '
                                 'WHERE k = ?', (key,))
        row = cur.fetchone()
        if not row:
            if default != Ellipsis:
                return default
            raise KeyError(key)
        return self._unserialize(_DType(row[0]), row[1])

    def get_many(self, keys: Iterable[str]) -> Dict[str, Any]:
        """Get many values from the database.

        Args:
            keys: An iterable of keys to return.

        Returns:
            A dict where with only the keys found on the database, and their
            respective values.

        """
        assert self._conn
        tkeys = tuple(keys)
        rows = self._conn.execute(f'SELECT k, t, v FROM {self._table} WHERE '
                                  f'k IN ({", ".join(["?"] * len(tkeys))})',
                                  tkeys)
        return {r[0]: self._unserialize(_DType(r[1]), r[2])
                for r in rows.fetchall()}

    def get_glob(self, glob_key: str) -> Dict[str, Any]:
        """Get many values using a glob pattern.

        Similar to `get_many()`, but using a glob pattern.

        Args:
            glob_key: A shell-like wildcard for matching keys.

        Returns:
            A dict where keys are the keys matching `glob_key` found
            on the database, and their respective values.

        """
        assert self._conn
        rows = self._conn.execute('SELECT k, t, v '
                                  f'FROM {self._table} '
                                  'WHERE k GLOB ?', (glob_key,))
        return {r[0]: self._unserialize(_DType(r[1]), r[2])
                for r in rows.fetchall()}

    def set_many(self, kvdict: Mapping[str, Any]) -> None:
        """Set many values at once.

        Args:
            kvdict: A mapping of keys to values.

        """
        assert self._conn
        self._conn.execute(f'INSERT OR REPLACE INTO {self._table} (k, t, v) '
                           f'VALUES {", ".join(["(?, ?, ?)"] * len(kvdict))}',
                           tuple(p[i]
                                 for p
                                 in (((i[0],) + tuple(self._serialize(i[1])))
                                     for i in kvdict.items())
                                 for i
                                 in range(0, 3)))

    def remove(self, key: str) -> None:
        """Remove a key from the database.

        Args:
            key: The key to remove.

        Raises:
            KeyError: If the key is not found.

        """
        assert self._conn
        cur = self._conn.execute(f'DELETE FROM {self._table} WHERE k = ?',
                                 (key,))
        if cur.rowcount == 0:
            raise KeyError(key)

    def remove_many(self, keys: Iterable[str]) -> None:
        """Remove many keys from the database at once.

        Nonexistent keys are silently ignored.

        Args:
            keys: Iterable of keys to remove.
        """
        assert self._conn
        tkeys = tuple(keys)
        self._conn.execute(f'DELETE FROM {self._table} WHERE '
                           f'k IN ({", ".join(["?"] * len(tkeys))})',
                           tkeys)
