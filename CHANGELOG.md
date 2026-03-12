# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

Entries marked as **BC BREAK** indicate backward-incompatible changes.

## [Unreleased]

### Changed
- **BC BREAK** Minimum Python version is now 3.11 (was 3.7). This enables
  modern type hint syntax and removes compatibility code for older Python
  versions.
- **BC BREAK** Keys are now case-sensitive. Previously, the database used
  case-insensitive collation, so `'Foo'` and `'foo'` referred to the same key.
  Existing tables using the old schema will continue to work with the old
  behavior, but new tables created with `create_schema()` will use
  case-sensitive keys.
- Tables created with `create_schema()` now use SQLite `WITHOUT ROWID` storage
  for better performance and smaller database size

## [0.1.3] - 2026-03-11

Pickle safety improvements and bug fixes.

### Added
- Add explicit pickle safety mode with user-facing warning
- Enforce string key contract: non-string keys raise `TypeError`, empty
  strings raise `ValueError`

### Security
- Validate table names to prevent SQL injection via table identifier

### Fixed
- Fix precision loss when roundtripping large integers through the database
- Fix NaN roundtrip crash by storing as pickle and always deserializing
- Fix `set_many({})` crash by making empty batch a no-op
- Fix `remove_many([])` crash by making empty batch a no-op
- Fix integral float type loss (e.g., `1.0` returned as `1`)

## [0.1.2] - 2025-07-28

Package reorganization for better pip compatibility.

### Changed
- Reorganize package into standard "src" layout for better pip install
  experience

## [0.1.1] - 2023-03-14

Initial release of tinykv.

### Added
- Initial release
