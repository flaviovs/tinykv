TinyKV - a minimal key-value database for Python using SQLite3
==============================================================

TinyKV implements a key-value database interface for Python 3 using
the [sqlite3](https://docs.python.org/3/library/sqlite3.html) module
from the standard library. There is no dependency on external
packages.

TinyKV requires Python 3.7 or above.


Installation
------------

    pip install tinykv


Basic usage
-----------

First let’s import _sqlite3_ and the TinyKV module:

    >>> import sqlite3
    >>> import tinykv

TinyKV does not create a SQLite3 database to operate on. Instead, it
operate on connections managed by the caller. So let’s create a
database for using in the examples:

    >>> conn = sqlite3.connect(':memory:')

This is how you create a TinyKV object:

    >>> kv = tinykv.TinyKV(conn)
    Traceback (most recent call last):
        ...
    RuntimeError: Table 'kv' not found in the database

Oops... TinyKV does not create the database table it needs either. For
that the application can use `create_schema()`:

    >>> tinykv.create_schema(conn)

Let’s try again:

    >>> kv = tinykv.TinyKV(conn)
    >>> kv  # doctest: +ELLIPSIS
    <tinykv.TinyKV object at 0x...>

Now it works!


## Storing and retrieving data

Use `set()` and `get()` to store and retrieve data from a TinyKV
database, respectively:

    >>> kv.set('foo', 'bar')
    >>> kv.get('foo')
    'bar'

Trying to get a nonexistent key raises _KeyError_:

    >>> kv.get('not-there')
    Traceback (most recent call last):
        ...
    KeyError: 'not-there'

You can pass a default value to be returned if a key does not exist:

    >>> kv.get('not-there', 'but-try-this')
    'but-try-this'


## Value data types

You can store any regular Python scalar in the key-value database:

    >>> kv.set('foo', None)
    >>> foo = kv.get('foo')
    >>> foo is None
    True

    >>> kv.set('one', 1)
    >>> kv.get('one')
    1

    >>> kv.set('pi', 3.1415926)
    >>> kv.get('pi')
    3.1415926

Same for container objects:

    >>> kv.set('a_list', ['one', 'two', 'three'])
    >>> kv.get('a_list')
    ['one', 'two', 'three']

In fact, you can store any pickable object:

    >>> import datetime
    >>>
    >>> kv.set('a-long-time-ago', datetime.datetime(2022, 3, 19, 20, 15, 5))

    >>> a_long_time_ago = kv.get('a-long-time-ago')

    >>> a_long_time_ago
    datetime.datetime(2022, 3, 19, 20, 15, 5)

    >>> type(a_long_time_ago)
    <class 'datetime.datetime'>


## Removing entries

Use `remove()` to remove entries from the database:

    >>> kv.set('foo', 'bar')
    >>> kv.remove('foo')
    >>> kv.get('foo')
    Traceback (most recent call last):
        ...
    KeyError: 'foo'


## Working with multiple entries

Use `set_many()` with a key-value _dict_ to store multiple entries at
once:

    >>> kv.set_many({
    ...     'one': 1,
    ...     'two': 2,
    ... })
    >>> kv.get('one')
    1
    >>> kv.get('two')
    2

Call `get_many()` to retrieve many entries. The function returns a
_dict_ of all keys-values found in the database:

    >>> kv.set('one', 1)
    >>> kv.set('two', 2)
    >>> kv.get_many(['one', 'two', 'not-there'])
    {'one': 1, 'two': 2}

You can also use `get_glob()` to fetch entries based a glob pattern:

    >>> kv.set('foo:abc', 1)
    >>> kv.set('foo:xyz', 2)
    >>> kv.set('bar:123', 3)

    >>> kv.get_glob('foo:*')
    {'foo:abc': 1, 'foo:xyz': 2}

    >>> kv.get_glob('*:123')
    {'bar:123': 3}

Notice that `get_many()` and `get_glob()` never raise _KeyError_ for
nonexistent keys. Instead, those keys are simple not present in the
returned _dict_.

You can also remove many entries in one call with
`remove_many()`. Nonexistent keys are silently ignored:

    >>> kv.get('one')
    1

    >>> kv.remove_many(['one', 'not-there'])
    >>> kv.get('one')
    Traceback (most recent call last):
        ...
    KeyError: 'one'


Miscellaneous
-------------

- TinyKV keys must be string scalars.

- TinyKV does not open or manage transactions Also, it operates both
  in autocommit and non-autocommit mode. All operations are atomic.

- Naturally, if the connection handle is not in autocommit mode, you
  must call `commit()` on the connection to save the data.

- The connection handle is available in the read-only attribute `conn`
  of the TinyKV object:

        >>> kv.conn  # doctest: +ELLIPSIS
        <sqlite3.Connection object at 0x...>

- By default the table used by TinyKV is called _kv_. You can change
  that by passing a `table` argument to `create_schema()` and the
  TinyKV constructor:

        >>> CUSTOM_TABLE = 'custom_kv'
        >>>
        >>> tinykv.create_schema(conn, table=CUSTOM_TABLE)
        >>>
        >>> custom_kv = tinykv.TinyKV(conn, table=CUSTOM_TABLE)


Questions? Bugs? Suggestions?
-----------------------------
Visit https://github.com/flaviovs/tinykv
