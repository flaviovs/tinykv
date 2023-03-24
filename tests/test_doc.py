import doctest
from pathlib import Path

import tinykv


def load_tests(_loader, tests, _ignore):  # type: ignore[no-untyped-def]
    tests.addTests(doctest.DocFileSuite('../README.md'))

    tests.addTests(doctest.DocTestSuite(tinykv))
    for path in Path(tinykv.__file__).parent.glob('*.py'):
        module = path.stem
        if module != '__init__':
            tests.addTests(doctest.DocTestSuite(f'tinykv.{module}'))

    return tests
