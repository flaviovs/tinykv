# TinyKV Project Instructions

Lightweight Python SQLite key-value store.

Follow these instructions strictly.

---

## Stack

- Python 3.11+

---

## Python Guidelines

- PEP 8.
- Maximum line length: 79 characters.
- Line length maximum.
- PEP 257 docstring using Google-style formatting.

---

## Versioning

This project adheres to Semantic Versioning.

You must follow this workflow when asked to bump version:

1. Ensure a clean work tree, stop and ask for guidance if dirty.
2. Analyze all the changes since last version.
3. Determine with version to bump: **major**, **minor**, or **patch**.
4. If **patch**:
   - update changelog and module version,
   - otherwise, when **major** or **minor** version, ask user for consent to bump.

Do not:

- Bump version without user consent.
- Bump **major** and **minor** versions without confirmation.

After bumping:

- Ensure in `CHANGELOG.md` that:
  - Pending changes are under new version.
  - Section `[Unreleased]` is still present, but empty.
- Run `pip install --upgrade --group=dev -e .`.

---

## Strict Rules

Prioritize:

- Correctness.
- Maintainability.
- Simplicity.
- Idiomatic Pythonic constructions.
- Strong foundations that facilitate future expansion.

You must:

- Use type annotations.
- Use `str | None` and built-in generics such as `list[T]`.
- Add docstrings to all public interfaces.

You must not:

- Add imports inside functions or methods, or between non import statements.
- Make unrelated refactors or move logic across layers unnecessarily.
- Add `# type: ignore` or use `cast()` under `src/`; stop and ask for guidance if unavoidable.

---

## Commands

Single test:

```bash
python -m unittest tests.test_tinykv.TestKV.test_set_get
```

Test module:

```bash
python -m unittest tests/test_tinykv.py
```

All tests, including README and docstring doctests:

```bash
python -m unittest tests/test_*.py
```

Auto-fix and format:

```bash
ruff check --fix
ruff format
```

Global QA checks:

```bash
ruff check
mypy
```

---

## Git

Only read-only Git commands are allowed.

Do not:

- Rewrite history.
- Force push.
- Use destructive Git commands.
- Commit unless explicitly requested, even if committed before.

---

## Changelog Policy.

Keep `CHANGELOG.md` updated for user-visible changes.

Sections names:

- Added
- Changed
- Deprecated
- Removed
- Fixed
- Security

You must:

- Write entries for humans.
- Group by change type.
- Wrap lines at 80 columns.
- Document only behaviour changes.

You must not:

- Just replicate details already in the `README.md` file.
- Change past entries under released versions.
- Document internal changes.

---

## Change Workflow

You must follow this workflow, no exceptions:

1. Inspect existing patterns and conventions.
2. Implement changes.
3. Run Ruff auto-fix and format on changed Python files:
   - fix issues and repeat.
4. Run global code QA.
5. Run narrowest relevant tests:
   - fix issues and repeat.
7. Run full test suite:
   - fix issues and repeat,
   - add tests if coverage is below threshold and repeat.

Important:

- Do not run QA/tests on non code changes.
- Do not skip steps.
- Do not change order.

---

## Completion Criteria

- QA passes.
- Full test suite passes, including doctests.
- Documentation, changelog, and docstrings updated.
