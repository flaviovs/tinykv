import doctest
from pathlib import Path

import pluca


def load_tests(_loader, tests, _ignore):  # type: ignore[no-untyped-def]
    tests.addTests(doctest.DocFileSuite('../README.md'))

    tests.addTests(doctest.DocTestSuite(pluca))
    for path in Path(pluca.__file__).parent.glob('*.py'):
        module = path.stem
        if module != '__init__':
            tests.addTests(doctest.DocTestSuite(f'pluca.{module}'))

    return tests
