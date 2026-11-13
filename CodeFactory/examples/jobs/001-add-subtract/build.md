STATUS: READY
DRAFT_PR: https://github.com/acme/calculator/pull/14
BRANCH: factory/issue-12
ROUND: 1

## Acceptance criteria
- AC-1: met. `test_subtract_positive` in tests/test_calculator.py
- AC-2: met. `test_subtract_negative`, `test_subtract_zero`
- AC-3: met. `test_subtract_rejects_text` raises TypeError, as `add` does

## Subtasks
- s1 (backend-engineer): done. Red then green: 3 new tests failed on `subtract` missing, then passed.

## Commands run
- `python3 -m pytest -q` -> 12 passed

## Deferred debt
- Shared validation between `add` and `subtract` (see spec)

## Notes for reviewers
- Look at the TypeError check first; it mirrors `add` line for line.
