---
name: committing
description: Creates Git commits. Use when the user asks to commit changes, create a commit, or run git commit.
---

# Committing

Follow these instructions strictly when committing.

---

## Safety

- Create a commit only when the user explicitly requests one. Completing implementation, passing tests, or finishing QA does not imply authorization.
- Never amend an existing commit without explicit user consent.
- Do not include unrelated changes in the commit.

---

## Commit Gate

Before committing, verify against the project completion criteria and definition of done:

- The requested work is complete.
- Required project-specific QA and validation have passed.
- Required validation was run after the most recent modification to every affected file it covers.
- The intended commit contains only changes appropriate to this commit.

Validation may include project-local verification commands, tests, linters, type checks, builds, or other requirements defined by the project.

If any requirement is unmet or validation is stale, do not commit. Resolve the remaining requirement when within scope and rerun affected validation.

If the user explicitly instructs you to commit while skipping required validation, you may do so, but clearly report what was not validated. Never represent skipped validation as passing.

---

## Workflow

1. Verify the commit gate.
2. Inspect repository status and the changes to be committed.
3. Exclude unrelated changes.
4. Create one focused commit.
5. If it's a release commit: tag the commit.
6. Report the resulting commit and any validation intentionally skipped.

---

## Commit message

- Use imperative mood.
- Prefer a subject of 50 characters or fewer.
- Describe what the commit does rather than narrating the work performed.
- Add a body only when it provides necessary context not apparent from the subject or diff.
- Wrap body text at 72 columns.
- Release commits:
  - Commit and annotated message format: `Release version 0.1.2`.
  - Tag format: `v0.1.2`.
