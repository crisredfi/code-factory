# Spec: subtract function

Job: issue-12 · Issue: https://github.com/acme/calculator/issues/12 · Scope: small · Risk: LOW
Branch: factory/issue-12

## Goal
Expose `subtract(a, b)` from the calculator module, next to `add`, with the same error style.

## Platforms
- Backend/library only. Entry point: `src/calculator.py`.

## Acceptance criteria
- [ ] AC-1: `subtract(5, 3)` returns `2`.
- [ ] AC-2: `subtract(-4, -10)` returns `6`; `subtract(0, 7)` returns `-7`.
- [ ] AC-3: `subtract` rejects non-numeric input with the same exception type `add` uses.

## Test plan
- Unit tests in `tests/test_calculator.py`, written first (TDD): one per criterion.

## Out of scope
Division, parsing strings, a command line.

## Deferred debt
- `add` and `subtract` share validation code that could move to a helper. Not needed for this change.

## Open questions
none
