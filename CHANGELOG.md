# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

Entries marked as **BC BREAK** indicate backward-incompatible changes.

## [Unreleased]

Pickle safety improvements and bug fixes.

### Added
- Add explicit pickle safety mode with user-facing warning

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
