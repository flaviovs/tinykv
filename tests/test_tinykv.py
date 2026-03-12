import datetime
import math
import pickle
import sqlite3
import secrets
import tempfile
import unittest
import warnings
from pathlib import Path
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


class TestKV(unittest.TestCase):  # pylint: disable=too-many-public-methods

    def setUp(self) -> None:
        # pylint: disable-next=consider-using-with
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

    def test_create_schema_if_not_exists(self) -> None:
        create_schema(self._conn, if_not_exists=True)

    def test_set_replace(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)

        db.set('foo', 1)
        db.set('foo', 'bar')

        self.assertEqual(db.get('foo'), 'bar')

    def test_get_default(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        self.assertEqual(db.get('foo', 'bar'), 'bar')
        self.assertIsNone(db.get('foo', None))

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

    def test_compat_mode_allows_pickle_roundtrip(self) -> None:
        db = TinyKV(self._conn, allow_pickle=True)
        dt = datetime.datetime(2022, 3, 19, 20, 15, 5)

        db.set('dt', dt)

        self.assertEqual(db.get('dt'), dt)

    def test_implicit_allow_pickle_warns(self) -> None:
        with self.assertWarnsRegex(FutureWarning, 'allow_pickle'):
            TinyKV(self._conn)

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
