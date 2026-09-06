import doctest
import unittest
from pathlib import Path

import tinykv


def load_tests(
    _loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    _ignore: object,
) -> unittest.TestSuite:
    tests.addTests(doctest.DocFileSuite('../README.md'))

    tests.addTests(doctest.DocTestSuite(tinykv))
    for path in Path(tinykv.__file__).parent.glob('*.py'):
        module = path.stem
        if module != '__init__':
            tests.addTests(doctest.DocTestSuite(f'tinykv.{module}'))

    return tests
