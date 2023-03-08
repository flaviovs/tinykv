import datetime
import unittest
import sqlite3
import secrets
import tempfile
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


class TestKV(unittest.TestCase):

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
        db = TinyKV(self._conn)

        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                db.set(k, v)
                self.assertEqual(db.get(k), v)

    def test_set_replace(self) -> None:
        db = TinyKV(self._conn)

        db.set('foo', 1)
        db.set('foo', 'bar')

        self.assertEqual(db.get('foo'), 'bar')

    def test_get_default(self) -> None:
        db = TinyKV(self._conn)
        self.assertEqual(db.get('foo', 'bar'), 'bar')
        self.assertIsNone(db.get('foo', None))

    def test_set_get_persist(self) -> None:
        db = TinyKV(self._conn)

        for k, v in _TEST_DATA.items():
            db.set(k, v)

        self._conn.commit()
        self._conn.close()

        self._conn = sqlite3.connect(self._path)
        db2 = TinyKV(self._conn)
        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                self.assertEqual(db2.get(k), v)

    def test_get_many(self) -> None:
        db = TinyKV(self._conn)

        for k, v in _TEST_DATA.items():
            db.set(k, v)

        self.assertEqual(db.get_many(_TEST_DATA.keys()), _TEST_DATA)

    def test_get_many_nonexisting(self) -> None:
        db = TinyKV(self._conn)

        db.set('foo', 1)
        db.set('bar', 2)

        self.assertEqual(db.get_many(('foo', 'bar', 'not-there')),
                         {'foo': 1, 'bar': 2})

    def test_get_glob(self) -> None:
        db = TinyKV(self._conn)

        db.set('foo:abc', 1)
        db.set('foo:xyz', 2)
        db.set('bar:abc', 3)

        self.assertEqual(db.get_glob('foo:*'), {'foo:abc': 1, 'foo:xyz': 2})

    def test_set_many(self) -> None:
        db = TinyKV(self._conn)

        db.set_many(_TEST_DATA)

        for k, v in _TEST_DATA.items():
            with self.subTest(k=k):
                self.assertEqual(db.get(k), v)

    def test_remove(self) -> None:
        db = TinyKV(self._conn)

        db.set('foo', 'bar')

        db.remove('foo')

        with self.assertRaises(KeyError):
            db.get('foo')

    def test_remove_nonexistent(self) -> None:
        db = TinyKV(self._conn)
        with self.assertRaises(KeyError):
            db.remove('nonexistent')

    def test_remove_many(self) -> None:
        db = TinyKV(self._conn)

        db.set('foo', 'bar')
        db.set('bar', 'bar')

        db.remove_many(('foo', 'bar'))

        with self.assertRaises(KeyError):
            db.get('foo')

        with self.assertRaises(KeyError):
            db.get('bar')
