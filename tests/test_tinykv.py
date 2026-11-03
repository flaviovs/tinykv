import builtins
import datetime
import math
import pickle
import secrets
import sqlite3
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from tinykv import TinyKV, _Savepoint, create_schema

_TEST_DATA = {
    "none": None,
    "foo": "bar",
    "bytes": secrets.token_bytes(20),
    "maybe": True,
    "one": 1,
    "pi": 3.1415926,
    "complex": complex(1, 2),
    "now": datetime.datetime.now(),
}


class _Explosive:
    def __reduce__(self) -> tuple[object, tuple[str, ...]]:
        return (print, ("PICKLE_EXECUTED",))


class TestKV(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._path = Path(self._tempdir.name) / "db.sqlite3"
        self._conn = sqlite3.connect(self._path)
        create_schema(self._conn)

    def tearDown(self) -> None:
        self._conn.close()
        self._tempdir.cleanup()

    def test_set_get(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                db.set(k, v)
                self.assertEqual(db.get(k), v)

    def test_create_schema_existing_table_raises_by_default(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            create_schema(self._conn)

    def test_create_schema_rejects_non_string_table(self) -> None:
        with self.assertRaisesRegex(TypeError, "table name must be a string"):
            create_schema(self._conn, table=123)  # type: ignore[arg-type]

    def test_create_schema_rejects_trailing_newline_table_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid table name"):
            create_schema(self._conn, table="kv\n")

    def test_valid_custom_table_name_roundtrip(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        create_schema(conn, table="table_123")
        db = TinyKV(conn, table="table_123", allow_pickle=True)

        db.set("foo", "bar")

        self.assertEqual(db.get("foo"), "bar")

    def test_create_schema_if_not_exists(self) -> None:
        create_schema(self._conn, if_not_exists=True)

    def test_current_schema_does_not_load_migrations(self) -> None:
        with mock.patch(
            "builtins.__import__", wraps=builtins.__import__
        ) as import_mock:
            TinyKV(self._conn)

        self.assertFalse(
            any(
                call.args[0] == "_migrations" and call.args[-1] == 1
                for call in import_mock.call_args_list
            )
        )

    def test_create_schema_records_v2_and_migrates_v1(self) -> None:
        self.assertEqual(
            self._conn.execute(
                "SELECT v FROM kv WHERE k = '__tinykv_schema_version'"
            ).fetchone(),
            (b"2",),
        )

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE kv (k TEXT NOT NULL, t TINYINT NOT NULL, "
            "v BLOB, PRIMARY KEY (k)) WITHOUT ROWID"
        )
        db = TinyKV(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(kv)")}
        self.assertIn("e", columns)
        self.assertEqual(
            conn.execute(
                "SELECT v FROM kv WHERE k = '__tinykv_schema_version'"
            ).fetchone(),
            (b"2",),
        )
        db.set("retained", "value")
        self.assertEqual(db.get("retained"), "value")

    def test_migration_is_rolled_back_with_caller_transaction(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE kv (k TEXT NOT NULL, t TINYINT NOT NULL, "
            "v BLOB, PRIMARY KEY (k)) WITHOUT ROWID"
        )
        conn.commit()
        conn.execute("BEGIN")

        TinyKV(conn)
        conn.rollback()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(kv)")}
        self.assertNotIn("e", columns)

    def test_reserved_word_table_name(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        create_schema(conn, table="select")
        db = TinyKV(conn, table="select", allow_pickle=True)

        db.set("foo", "bar")
        db.set_many({"one": 1, "two": 2})
        self.assertEqual(db.get("foo"), "bar")
        self.assertEqual(
            db.get_many(["foo", "one", "missing"]), {"foo": "bar", "one": 1}
        )
        self.assertEqual(db.get_glob("*"), {"foo": "bar", "one": 1, "two": 2})
        db.remove("foo")
        db.remove_many(["one", "two"])
        self.assertEqual(db.get_many(["foo", "one", "two"]), {})

    def test_attached_database_table_name_roundtrip(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        create_schema(conn)
        conn.execute("ATTACH DATABASE ':memory:' AS attached")
        create_schema(conn, table="attached.kv")
        db = TinyKV(conn, table="attached.kv", allow_pickle=True)
        main_db = TinyKV(conn, allow_pickle=True)

        db.set("foo", "attached")
        db.set_many({"one": 1, "two": 2})

        self.assertEqual(db.get("foo"), "attached")
        self.assertEqual(
            db.get_many(["foo", "one", "missing"]),
            {"foo": "attached", "one": 1},
        )
        self.assertEqual(
            db.get_glob("*"), {"foo": "attached", "one": 1, "two": 2}
        )
        self.assertEqual(main_db.get("foo", "missing"), "missing")

        db.remove("foo")
        db.remove_many(["one", "two"])
        self.assertEqual(db.get_many(["foo", "one", "two"]), {})

    def test_qualified_table_name_validation(self) -> None:
        for table in (
            "attached..kv",
            "attached.kv.extra",
            ".kv",
            "attached.kv; DROP TABLE other;",
        ):
            with (
                self.subTest(table=table),
                self.assertRaisesRegex(ValueError, "Invalid table name"),
            ):
                create_schema(self._conn, table=table)

    def test_table_name_injection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid table name"):
            create_schema(self._conn, table="kv; DROP TABLE other;")

    def test_set_replace(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo", 1)
        db.set("foo", "bar")

        self.assertEqual(db.get("foo"), "bar")

    def test_set_expiration_and_gc(self) -> None:
        db = TinyKV(self._conn)
        db.set("expired", "value", ttl=0)

        with self.assertRaises(KeyError):
            db.get("expired")
        self.assertEqual(
            self._conn.execute(
                "SELECT COUNT(*) FROM kv WHERE k = 'expired'"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(db.gc(), 1)
        self.assertEqual(db.gc(), 0)

    def test_expired_keys_are_absent_to_all_key_operations(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        db.set("expired", 5, ttl=0)

        self.assertEqual(db.get("expired", "default"), "default")
        self.assertEqual(db.get_many(["expired"]), {})
        self.assertEqual(db.get_glob("expired"), {})
        self.assertEqual(db.inc("expired"), 1)
        self.assertEqual(db.get("expired"), 1)
        db.set("expired", 5, ttl=0)
        with self.assertRaises(KeyError):
            db.remove("expired")
        self.assertEqual(
            self._conn.execute(
                "SELECT COUNT(*) FROM kv WHERE k = 'expired'"
            ).fetchone(),
            (1,),
        )

    def test_set_expiration_validation(self) -> None:
        db = TinyKV(self._conn)
        for value in (True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                db.set("key", "value", ttl=value)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            db.set("key", "value", ttl=-1)

    def test_ttl_rejects_expiration_beyond_sqlite_integer_range(self) -> None:
        db = TinyKV(self._conn)
        too_large = 1 << 63
        operations = (
            lambda: db.set("set", "value", ttl=too_large),
            lambda: db.set_ttl("set_ttl", too_large),
            lambda: db.set_many({"batch": 1}, ttl=too_large),
            lambda: db.add("create", 1, create_ttl=too_large),
            lambda: db.add("update", 1, update_ttl=too_large),
        )

        for operation in operations:
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(
                    ValueError, "too large for a SQLite INTEGER timestamp"
                ),
            ):
                operation()

    def test_set_ttl_updates_and_clears_existing_expiration(self) -> None:
        db = TinyKV(self._conn)
        db.set("key", "value")

        db.set_ttl("key", 60)
        expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'key'"
        ).fetchone()[0]
        self.assertGreater(expiration, int(time.time()))
        self.assertEqual(db.get("key"), "value")

        db.set_ttl("key", None)
        self.assertIsNone(
            self._conn.execute("SELECT e FROM kv WHERE k = 'key'").fetchone()[
                0
            ]
        )
        self.assertEqual(db.get("key"), "value")

    def test_set_ttl_missing_or_expired_key_raises(
        self,
    ) -> None:
        db = TinyKV(self._conn)
        with self.assertRaises(KeyError):
            db.set_ttl("missing", 60)

        db.set("expired", "value", ttl=0)
        with self.assertRaises(KeyError):
            db.set_ttl("expired", 60)
        self.assertEqual(
            self._conn.execute(
                "SELECT COUNT(*) FROM kv WHERE k = 'expired'"
            ).fetchone(),
            (1,),
        )

    def test_set_ttl_validates_key_and_ttl(self) -> None:
        db = TinyKV(self._conn)
        with self.assertRaises(TypeError):
            db.set_ttl(1, 60)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            db.set_ttl("", 60)
        with self.assertRaises(ValueError):
            db.set_ttl("key", -1)
        for ttl in (True, 1.5, "1"):
            with self.subTest(ttl=ttl), self.assertRaises(TypeError):
                db.set_ttl("key", ttl)  # type: ignore[arg-type]

    def test_set_ttl_respects_caller_transaction(self) -> None:
        db = TinyKV(self._conn)
        db.set("key", "value", ttl=30)
        self._conn.commit()
        original_expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'key'"
        ).fetchone()[0]

        self._conn.execute("BEGIN")
        db.set_ttl("key", 120)
        self._conn.rollback()

        self.assertEqual(
            self._conn.execute("SELECT e FROM kv WHERE k = 'key'").fetchone()[
                0
            ],
            original_expiration,
        )

    def test_add_preserves_unexpired_expiration(self) -> None:
        db = TinyKV(self._conn)
        db.set("counter", 1, ttl=30)
        expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'counter'"
        ).fetchone()

        self.assertEqual(db.add("counter", 1), 2)
        self.assertEqual(
            self._conn.execute(
                "SELECT e FROM kv WHERE k = 'counter'"
            ).fetchone(),
            expiration,
        )

    def test_add_uses_separate_create_and_update_ttls(self) -> None:
        db = TinyKV(self._conn)

        self.assertEqual(db.add("counter", 1, create_ttl=90, update_ttl=10), 1)
        create_expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'counter'"
        ).fetchone()[0]
        self.assertGreaterEqual(create_expiration, int(time.time()) + 89)

        self.assertEqual(
            db.add("counter", 1, create_ttl=300, update_ttl=20), 2
        )
        update_expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'counter'"
        ).fetchone()[0]
        self.assertGreaterEqual(update_expiration, int(time.time()) + 19)
        self.assertLess(update_expiration, create_expiration)

        db.add("counter", 1, update_ttl=None)
        self.assertIsNone(
            self._conn.execute(
                "SELECT e FROM kv WHERE k = 'counter'"
            ).fetchone()[0]
        )

    def test_inc_and_dec_forward_create_and_update_ttls(self) -> None:
        db = TinyKV(self._conn)

        self.assertEqual(db.inc("counter", create_ttl=60), 1)
        create_expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'counter'"
        ).fetchone()[0]
        self.assertEqual(db.dec("counter", update_ttl=20), 0)
        update_expiration = self._conn.execute(
            "SELECT e FROM kv WHERE k = 'counter'"
        ).fetchone()[0]

        self.assertGreaterEqual(create_expiration, int(time.time()) + 59)
        self.assertGreaterEqual(update_expiration, int(time.time()) + 19)
        self.assertLess(update_expiration, create_expiration)

    def test_arithmetic_ttl_validation(self) -> None:
        db = TinyKV(self._conn)
        for method in (db.add, db.inc, db.dec):
            with self.subTest(method=method.__name__):
                with self.assertRaises(TypeError):
                    method("key", 1, create_ttl=True)
                with self.assertRaises(TypeError):
                    method("key", 1, update_ttl=1.5)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    method("key", 1, create_ttl=-1)
                with self.assertRaises(ValueError):
                    method("key", 1, update_ttl=-1)

    def test_add_inc_dec(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        self.assertEqual(db.inc("counter"), 1)
        self.assertEqual(db.inc("counter", 4), 5)
        self.assertEqual(db.dec("counter", 2), 3)
        self.assertEqual(db.add("counter", -5), -2)
        self.assertEqual(db.get("counter"), -2)

    def test_add_zero_creates_missing_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        self.assertEqual(db.inc("counter", 0), 0)
        self.assertEqual(db.get("counter"), 0)

    def test_add_supports_arbitrarily_large_integers(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        value = 10**100

        self.assertEqual(db.add("counter", value), value)
        self.assertEqual(db.add("counter", -2 * value), -value)
        self.assertEqual(db.get("counter"), -value)

    def test_add_rejects_invalid_amounts_and_existing_values(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        for amount in (True, False, 1.0, "1"):
            with (
                self.subTest(amount=amount),
                self.assertRaises(TypeError),
            ):
                db.add("counter", amount)  # type: ignore[arg-type]

        for method in (db.inc, db.dec):
            with self.subTest(method=method.__name__):
                with self.assertRaises(ValueError):
                    method("counter", -1)
                with self.assertRaises(TypeError):
                    method("counter", True)

        for value in (True, 1.5, "1", None):
            db.set("counter", value)
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    db.add("counter", 1)
                self.assertEqual(db.get("counter"), value)

    def test_add_is_atomic_inside_caller_transaction(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self._conn.execute("SAVEPOINT caller")

        self.assertEqual(db.inc("counter"), 1)
        self.assertTrue(self._conn.in_transaction)
        self._conn.execute("ROLLBACK TO caller")
        self._conn.execute("RELEASE caller")

        with self.assertRaises(KeyError):
            db.get("counter")

    def test_add_is_atomic_in_autocommit_mode(self) -> None:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        create_schema(conn)
        db = TinyKV(conn)

        self.assertEqual(db.inc("counter"), 1)
        self.assertFalse(conn.in_transaction)

    def test_add_is_atomic_without_an_open_transaction(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        create_schema(conn)
        db = TinyKV(conn)

        self.assertFalse(conn.in_transaction)
        self.assertEqual(db.inc("counter"), 1)
        self.assertFalse(conn.in_transaction)

    def test_add_is_atomic_inside_explicit_transaction(self) -> None:
        db = TinyKV(self._conn)
        self._conn.execute("BEGIN")

        self.assertEqual(db.inc("counter"), 1)
        self.assertTrue(self._conn.in_transaction)
        self._conn.rollback()

        with self.assertRaises(KeyError):
            db.get("counter")

    def test_savepoints_can_be_nested_with_the_same_label(self) -> None:
        with _Savepoint(self._conn):
            self._conn.execute(
                "INSERT INTO kv (k, t, v) VALUES ('outer', 2, X'31')"
            )
            with (
                self.assertRaisesRegex(ValueError, "rollback inner"),
                _Savepoint(self._conn),
            ):
                self._conn.execute(
                    "INSERT INTO kv (k, t, v) VALUES ('inner', 2, X'32')"
                )
                raise ValueError("rollback inner")

            self.assertEqual(
                self._conn.execute(
                    "SELECT k FROM kv WHERE k = 'outer' ORDER BY k"
                ).fetchall(),
                [("outer",)],
            )

        self.assertEqual(
            self._conn.execute(
                "SELECT k FROM kv WHERE k = 'outer'"
            ).fetchall(),
            [("outer",)],
        )

    def test_get_default(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self.assertEqual(db.get("foo", "bar"), "bar")
        self.assertIsNone(db.get("foo", None))

        self.assertIs(db.get("missing", Ellipsis), Ellipsis)
        with self.assertRaises(KeyError):
            db.get("missing")

    def test_set_get_persist(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        for k, v in _TEST_DATA.items():
            db.set(k, v)

        self._conn.commit()
        self._conn.close()

        self._conn = sqlite3.connect(self._path)
        db2 = TinyKV(self._conn, allow_pickle=True)
        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                self.assertEqual(db2.get(k), v)

    def test_get_many(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        for k, v in _TEST_DATA.items():
            db.set(k, v)

        self.assertEqual(db.get_many(_TEST_DATA.keys()), _TEST_DATA)

    def test_get_many_nonexisting(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo", 1)
        db.set("bar", 2)

        self.assertEqual(
            db.get_many(("foo", "bar", "not-there")), {"foo": 1, "bar": 2}
        )

    def test_get_many_uses_savepoint_for_chunked_reads(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        db.set_many({"first": 1, "second": 2})
        self._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 1)
        statements: list[str] = []
        self._conn.set_trace_callback(statements.append)

        self.assertEqual(
            db.get_many(["first", "second"]), {"first": 1, "second": 2}
        )

        self._conn.set_trace_callback(None)
        self.assertTrue(
            any(
                statement.startswith("SAVEPOINT __tinykv_")
                for statement in statements
            )
        )
        self.assertTrue(
            any(
                statement.startswith("RELEASE __tinykv_")
                for statement in statements
            )
        )
        self.assertEqual(
            sum(
                statement.startswith("SELECT k, t, v")
                for statement in statements
            ),
            2,
        )

    def test_batch_methods_chunk_at_connection_variable_limit(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 3)
        values = {f"key-{i}": i for i in range(4)}

        db.set_many(values)
        self.assertEqual(db.get_many(values), values)
        db.remove_many(values)
        self.assertEqual(db.get_many(values), {})

    def test_batch_methods_handle_empty_and_small_inputs(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        self.assertEqual(db.get_many([]), {})
        db.set_many({"key": "value"})
        self.assertEqual(db.get_many(["key"]), {"key": "value"})
        db.remove_many(["key"])
        self.assertEqual(db.get_many(["key"]), {})

    def test_get_glob(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo:abc", 1)
        db.set("foo:xyz", 2)
        db.set("bar:abc", 3)

        self.assertEqual(db.get_glob("foo:*"), {"foo:abc": 1, "foo:xyz": 2})

    def test_keys_and_iter_keys(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo:abc", 1)
        db.set("foo:xyz", 2)
        db.set("bar:abc", 3)
        db.set("expired", "value", ttl=0)

        self.assertEqual(set(db.keys()), {"foo:abc", "foo:xyz", "bar:abc"})
        self.assertEqual(set(db.keys("foo:*")), {"foo:abc", "foo:xyz"})
        self.assertEqual(set(db.iter_keys("foo:*")), {"foo:abc", "foo:xyz"})

    def test_keys_only_selects_key_column(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        db.set("foo", "bar")
        statements: list[str] = []
        self._conn.set_trace_callback(statements.append)
        try:
            self.assertEqual(db.keys(), ["foo"])
        finally:
            self._conn.set_trace_callback(None)

        self.assertTrue(
            any(
                statement.startswith("SELECT k FROM")
                for statement in statements
            )
        )

    def test_set_many(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set_many(_TEST_DATA)

        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                self.assertEqual(db.get(k), v)

    def test_set_many_with_ttl(self) -> None:
        db = TinyKV(self._conn)

        db.set_many({"one": 1, "two": 2}, ttl=60)

        expirations = self._conn.execute(
            "SELECT e FROM kv WHERE k IN ('one', 'two') ORDER BY k"
        ).fetchall()
        self.assertEqual(len(expirations), 2)
        self.assertEqual(expirations[0], expirations[1])
        self.assertGreater(expirations[0][0], int(time.time()))
        self.assertEqual(db.get_many(["one", "two"]), {"one": 1, "two": 2})

    def test_set_many_with_zero_ttl_leaves_rows_for_gc(self) -> None:
        db = TinyKV(self._conn)

        db.set_many({"one": 1, "two": 2}, ttl=0)

        self.assertEqual(db.get_many(["one", "two"]), {})
        self.assertEqual(
            self._conn.execute(
                "SELECT COUNT(*) FROM kv WHERE k IN ('one', 'two')"
            ).fetchone(),
            (2,),
        )
        self.assertEqual(db.gc(), 2)

    def test_set_many_validates_ttl_even_for_empty_mapping(self) -> None:
        db = TinyKV(self._conn)
        for ttl in (True, 1.5, "1"):
            with self.subTest(ttl=ttl), self.assertRaises(TypeError):
                db.set_many({}, ttl=ttl)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            db.set_many({}, ttl=-1)

    def test_set_many_empty_mapping(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set_many({})

    def test_remove(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo", "bar")

        db.remove("foo")

        with self.assertRaises(KeyError):
            db.get("foo")

    def test_remove_nonexistent(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaises(KeyError):
            db.remove("nonexistent")

    def test_remove_many(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("foo", "bar")
        db.set("bar", "bar")

        db.remove_many(("foo", "bar"))

        with self.assertRaises(KeyError):
            db.get("foo")

        with self.assertRaises(KeyError):
            db.get("bar")

    def test_remove_many_empty(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.remove_many([])

    def test_safe_mode_rejects_pickle_on_set(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        with self.assertRaisesRegex(ValueError, "allow_pickle=True"):
            db.set("now", datetime.datetime.now())

    def test_safe_mode_rejects_existing_pickled_row(self) -> None:
        payload = pickle.dumps(datetime.datetime(2022, 3, 19, 20, 15, 5))
        self._conn.execute(
            "INSERT INTO kv (k, t, v) VALUES (?, ?, ?)",
            ("pickled", 6, payload),
        )

        db = TinyKV(self._conn, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, "allow_pickle=False"):
            db.get("pickled")

    def test_malformed_rows_raise_value_error(self) -> None:
        rows = [
            ("none", 1, b"not-none", "NONE"),
            ("string", 2, b"\xff", "STRING"),
            ("bytes", 3, None, "BYTES"),
            ("bool", 4, 2, "BOOL"),
            ("number", 5, "not-a-number", "NUMBER"),
            ("long", 7, b"not-an-integer", "LONG"),
            ("pickle", 6, b"not-a-pickle", "PICKLE"),
        ]
        self._conn.executemany(
            "INSERT INTO kv (k, t, v) VALUES (?, ?, ?)",
            (row[:3] for row in rows),
        )
        db = TinyKV(self._conn, allow_pickle=True)

        for key, _dtype, _data, logical_type in rows:
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    ValueError, f"Malformed data for type {logical_type}"
                ),
            ):
                db.get(key)

    def test_malformed_rows_raise_value_error_from_batch_reads(self) -> None:
        self._conn.execute(
            "INSERT INTO kv (k, t, v) VALUES (?, ?, ?)", ("bad", 2, b"\xff")
        )
        db = TinyKV(self._conn, allow_pickle=True)

        with (
            self.subTest(read="get_many"),
            self.assertRaisesRegex(ValueError, "type STRING"),
        ):
            db.get_many(["bad"])
        with (
            self.subTest(read="get_glob"),
            self.assertRaisesRegex(ValueError, "type STRING"),
        ):
            db.get_glob("bad")

    def test_invalid_type_tag_raises_value_error(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        with self.assertRaisesRegex(ValueError, "Unsupported data type 99"):
            db._decode_row(99, b"payload")  # pylint: disable=protected-access

    def test_safe_mode_does_not_execute_existing_pickled_row(self) -> None:
        payload = pickle.dumps(_Explosive())
        self._conn.execute(
            "INSERT INTO kv (k, t, v) VALUES (?, ?, ?)",
            ("pickled", 6, payload),
        )

        db = TinyKV(self._conn, allow_pickle=False)
        with (
            mock.patch("builtins.print") as print_mock,
            self.assertRaisesRegex(ValueError, "allow_pickle=False"),
        ):
            db.get("pickled")
        print_mock.assert_not_called()

    def test_compat_mode_allows_pickle_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        dt = datetime.datetime(2022, 3, 19, 20, 15, 5)

        db.set("dt", dt)

        self.assertEqual(db.get("dt"), dt)

    def test_implicit_allow_pickle_is_disabled(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter("always")
            db = TinyKV(self._conn)
            with self.assertRaisesRegex(ValueError, "allow_pickle=True"):
                db.set("dt", datetime.datetime.now())

        self.assertEqual(warns, [])

    def test_explicit_allow_pickle_true_does_not_warn(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter("always")
            TinyKV(self._conn, allow_pickle=True)
        self.assertEqual(warns, [])

    def test_explicit_allow_pickle_false_does_not_warn(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter("always")
            TinyKV(self._conn, allow_pickle=False)
        self.assertEqual(warns, [])

    def test_allow_pickle_rejects_non_boolean_values(self) -> None:
        for value in ("false", "true"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    TypeError, "allow_pickle must be a boolean"
                ),
            ):
                TinyKV(self._conn, allow_pickle=value)  # type: ignore[arg-type]

    def test_large_int_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        test_cases = [
            (2**53 - 1, "max safe integer"),
            (2**53, "just over max safe integer"),
            (2**53 + 1, "larger than max safe integer"),
            (10**20, "very large positive"),
            (-(10**20), "very large negative"),
            (0, "zero"),
            (1, "small positive"),
            (-1, "small negative"),
        ]

        for value, description in test_cases:
            with self.subTest(value=value, description=description):
                db.set("large_int", value)
                self.assertEqual(db.get("large_int"), value)

    def test_integral_float_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        test_cases = [
            (1.0, "one as float"),
            (0.0, "zero as float"),
            (-0.0, "negative zero as float"),
            (1.5, "one point five"),
            (-2.5, "negative two point five"),
        ]

        for value, description in test_cases:
            with self.subTest(value=value, description=description):
                db.set("integral_float", value)
                result = db.get("integral_float")
                self.assertEqual(result, value)
                self.assertIsInstance(result, float)

    def test_nan_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("nan", float("nan"))
        result = db.get("nan")
        self.assertTrue(math.isnan(result))

    def test_nan_roundtrip_safe_mode(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        db.set("nan", float("nan"))
        result = db.get("nan")
        self.assertTrue(math.isnan(result))

    def test_inf_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set("inf", float("inf"))
        self.assertEqual(db.get("inf"), float("inf"))

        db.set("neg_inf", float("-inf"))
        self.assertEqual(db.get("neg_inf"), float("-inf"))

    def test_set_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.set(123, "value")  # type: ignore[arg-type]

    def test_set_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.set("", "value")

    def test_set_rejects_reserved_key_prefix(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "reserved prefix"):
            db.set("__tinykv_custom", "value")

    def test_add_rejects_reserved_key_prefix(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "reserved prefix"):
            db.add("__tinykv_custom", 1)

    def test_set_many_rejects_reserved_key_prefix(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "reserved prefix"):
            db.set_many({"__tinykv_custom": "value"})

    def test_get_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.get(123)  # type: ignore[arg-type]

    def test_get_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.get("")

    def test_remove_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.remove(123)  # type: ignore[arg-type]

    def test_remove_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.remove("")

    def test_get_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.get_many([123])  # type: ignore[list-item]

    def test_get_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.get_many([""])

    def test_set_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.set_many({123: "value"})  # type: ignore[dict-item]

    def test_set_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.set_many({"": "value"})

    def test_remove_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.remove_many([123])  # type: ignore[list-item]

    def test_remove_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.remove_many([""])

    def test_get_glob_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, "must be a string"):
            db.get_glob(123)  # type: ignore[arg-type]

    def test_get_glob_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            db.get_glob("")
