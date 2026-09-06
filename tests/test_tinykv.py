import datetime
import math
import pickle
import secrets
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from tinykv import TinyKV, create_schema

_TEST_DATA = {
    'none': None,
    'foo': 'bar',
    'bytes': secrets.token_bytes(20),
    'maybe': True,
    'one': 1,
    'pi': 3.1415926,
    'complex': complex(1, 2),
    'now':  datetime.datetime.now(),
}


class _Explosive:
    def __reduce__(self) -> tuple[object, tuple[str, ...]]:
        return (print, ('PICKLE_EXECUTED',))


class TestKV(unittest.TestCase):

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._path = Path(self._tempdir.name) / 'db.sqlite3'
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
        with self.assertRaisesRegex(TypeError, 'table name must be a string'):
            create_schema(self._conn, table=123)  # type: ignore[arg-type]

    def test_create_schema_rejects_trailing_newline_table_name(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Invalid table name'):
            create_schema(self._conn, table='kv\n')

    def test_valid_custom_table_name_roundtrip(self) -> None:
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        create_schema(conn, table='table_123')
        db = TinyKV(conn, table='table_123', allow_pickle=True)

        db.set('foo', 'bar')

        self.assertEqual(db.get('foo'), 'bar')

    def test_create_schema_if_not_exists(self) -> None:
        create_schema(self._conn, if_not_exists=True)

    def test_reserved_word_table_name(self) -> None:
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        create_schema(conn, table='select')
        db = TinyKV(conn, table='select', allow_pickle=True)

        db.set('foo', 'bar')
        db.set_many({'one': 1, 'two': 2})
        self.assertEqual(db.get('foo'), 'bar')
        self.assertEqual(db.get_many(['foo', 'one', 'missing']),
                         {'foo': 'bar', 'one': 1})
        self.assertEqual(db.get_glob('*'),
                         {'foo': 'bar', 'one': 1, 'two': 2})
        db.remove('foo')
        db.remove_many(['one', 'two'])
        self.assertEqual(db.get_many(['foo', 'one', 'two']), {})

    def test_table_name_injection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Invalid table name'):
            create_schema(self._conn, table='kv; DROP TABLE other;')

    def test_set_replace(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('foo', 1)
        db.set('foo', 'bar')

        self.assertEqual(db.get('foo'), 'bar')

    def test_get_default(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self.assertEqual(db.get('foo', 'bar'), 'bar')
        self.assertIsNone(db.get('foo', None))

        self.assertIs(db.get('missing', Ellipsis), Ellipsis)
        with self.assertRaises(KeyError):
            db.get('missing')

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

        db.set('foo', 1)
        db.set('bar', 2)

        self.assertEqual(db.get_many(('foo', 'bar', 'not-there')),
                         {'foo': 1, 'bar': 2})

    def test_batch_methods_chunk_at_connection_variable_limit(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 3)
        values = {f'key-{i}': i for i in range(4)}

        db.set_many(values)
        self.assertEqual(db.get_many(values), values)
        db.remove_many(values)
        self.assertEqual(db.get_many(values), {})

    def test_batch_methods_handle_empty_and_small_inputs(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        self.assertEqual(db.get_many([]), {})
        db.set_many({'key': 'value'})
        self.assertEqual(db.get_many(['key']), {'key': 'value'})
        db.remove_many(['key'])
        self.assertEqual(db.get_many(['key']), {})

    def test_get_glob(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('foo:abc', 1)
        db.set('foo:xyz', 2)
        db.set('bar:abc', 3)

        self.assertEqual(db.get_glob('foo:*'), {'foo:abc': 1, 'foo:xyz': 2})

    def test_set_many(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set_many(_TEST_DATA)

        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                self.assertEqual(db.get(k), v)

    def test_set_many_empty_mapping(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set_many({})

    def test_remove(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('foo', 'bar')

        db.remove('foo')

        with self.assertRaises(KeyError):
            db.get('foo')

    def test_remove_nonexistent(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaises(KeyError):
            db.remove('nonexistent')

    def test_remove_many(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('foo', 'bar')
        db.set('bar', 'bar')

        db.remove_many(('foo', 'bar'))

        with self.assertRaises(KeyError):
            db.get('foo')

        with self.assertRaises(KeyError):
            db.get('bar')

    def test_remove_many_empty(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.remove_many([])

    def test_safe_mode_rejects_pickle_on_set(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        with self.assertRaisesRegex(ValueError, 'allow_pickle=True'):
            db.set('now', datetime.datetime.now())

    def test_safe_mode_rejects_existing_pickled_row(self) -> None:
        payload = pickle.dumps(datetime.datetime(2022, 3, 19, 20, 15, 5))
        self._conn.execute('INSERT INTO kv (k, t, v) VALUES (?, ?, ?)',
                           ('pickled', 6, payload))

        db = TinyKV(self._conn, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, 'allow_pickle=False'):
            db.get('pickled')

    def test_malformed_rows_raise_value_error(self) -> None:
        rows = [
            ('none', 1, b'not-none', 'NONE'),
            ('string', 2, b'\xff', 'STRING'),
            ('bytes', 3, None, 'BYTES'),
            ('bool', 4, 2, 'BOOL'),
            ('number', 5, 'not-a-number', 'NUMBER'),
            ('long', 7, b'not-an-integer', 'LONG'),
            ('pickle', 6, b'not-a-pickle', 'PICKLE'),
        ]
        self._conn.executemany('INSERT INTO kv (k, t, v) VALUES (?, ?, ?)',
                               (row[:3] for row in rows))
        db = TinyKV(self._conn, allow_pickle=True)

        for key, _dtype, _data, logical_type in rows:
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, f'Malformed data for type {logical_type}'
            ):
                db.get(key)

    def test_malformed_rows_raise_value_error_from_batch_reads(self) -> None:
        self._conn.execute('INSERT INTO kv (k, t, v) VALUES (?, ?, ?)',
                           ('bad', 2, b'\xff'))
        db = TinyKV(self._conn, allow_pickle=True)

        with self.subTest(read='get_many'), self.assertRaisesRegex(
            ValueError, 'type STRING'
        ):
            db.get_many(['bad'])
        with self.subTest(read='get_glob'), self.assertRaisesRegex(
            ValueError, 'type STRING'
        ):
            db.get_glob('bad')

    def test_invalid_type_tag_raises_value_error(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        with self.assertRaisesRegex(ValueError, 'Unsupported data type 99'):
            db._decode_row(99, b'payload')  # pylint: disable=protected-access

    def test_safe_mode_does_not_execute_existing_pickled_row(self) -> None:
        payload = pickle.dumps(_Explosive())
        self._conn.execute('INSERT INTO kv (k, t, v) VALUES (?, ?, ?)',
                           ('pickled', 6, payload))

        db = TinyKV(self._conn, allow_pickle=False)
        with (mock.patch('builtins.print') as print_mock,
              self.assertRaisesRegex(ValueError, 'allow_pickle=False')):
            db.get('pickled')
        print_mock.assert_not_called()

    def test_compat_mode_allows_pickle_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        dt = datetime.datetime(2022, 3, 19, 20, 15, 5)

        db.set('dt', dt)

        self.assertEqual(db.get('dt'), dt)

    def test_implicit_allow_pickle_is_disabled(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter('always')
            db = TinyKV(self._conn)
            with self.assertRaisesRegex(ValueError, 'allow_pickle=True'):
                db.set('dt', datetime.datetime.now())

        self.assertEqual(warns, [])

    def test_explicit_allow_pickle_true_does_not_warn(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter('always')
            TinyKV(self._conn, allow_pickle=True)
        self.assertEqual(warns, [])

    def test_explicit_allow_pickle_false_does_not_warn(self) -> None:
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter('always')
            TinyKV(self._conn, allow_pickle=False)
        self.assertEqual(warns, [])

    def test_allow_pickle_rejects_non_boolean_values(self) -> None:
        for value in ('false', 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(
                TypeError, 'allow_pickle must be a boolean'
            ):
                TinyKV(self._conn, allow_pickle=value)  # type: ignore[arg-type]

    def test_large_int_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        test_cases = [
            (2**53 - 1, 'max safe integer'),
            (2**53, 'just over max safe integer'),
            (2**53 + 1, 'larger than max safe integer'),
            (10**20, 'very large positive'),
            (-(10**20), 'very large negative'),
            (0, 'zero'),
            (1, 'small positive'),
            (-1, 'small negative'),
        ]

        for value, description in test_cases:
            with self.subTest(value=value, description=description):
                db.set('large_int', value)
                self.assertEqual(db.get('large_int'), value)

    def test_integral_float_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        test_cases = [
            (1.0, 'one as float'),
            (0.0, 'zero as float'),
            (-0.0, 'negative zero as float'),
            (1.5, 'one point five'),
            (-2.5, 'negative two point five'),
        ]

        for value, description in test_cases:
            with self.subTest(value=value, description=description):
                db.set('integral_float', value)
                result = db.get('integral_float')
                self.assertEqual(result, value)
                self.assertIsInstance(result, float)

    def test_nan_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('nan', float('nan'))
        result = db.get('nan')
        self.assertTrue(math.isnan(result))

    def test_nan_roundtrip_safe_mode(self) -> None:
        db = TinyKV(self._conn, allow_pickle=False)

        db.set('nan', float('nan'))
        result = db.get('nan')
        self.assertTrue(math.isnan(result))

    def test_inf_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('inf', float('inf'))
        self.assertEqual(db.get('inf'), float('inf'))

        db.set('neg_inf', float('-inf'))
        self.assertEqual(db.get('neg_inf'), float('-inf'))

    def test_set_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.set(123, 'value')  # type: ignore[arg-type]

    def test_set_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.set('', 'value')

    def test_get_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.get(123)  # type: ignore[arg-type]

    def test_get_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.get('')

    def test_remove_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.remove(123)  # type: ignore[arg-type]

    def test_remove_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.remove('')

    def test_get_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.get_many([123])  # type: ignore[list-item]

    def test_get_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.get_many([''])

    def test_set_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.set_many({123: 'value'})  # type: ignore[dict-item]

    def test_set_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.set_many({'': 'value'})

    def test_remove_many_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.remove_many([123])  # type: ignore[list-item]

    def test_remove_many_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.remove_many([''])

    def test_get_glob_rejects_non_string_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            db.get_glob(123)  # type: ignore[arg-type]

    def test_get_glob_rejects_empty_key(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            db.get_glob('')
