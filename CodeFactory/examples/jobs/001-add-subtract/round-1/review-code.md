VERDICT: PASS
RISK: LOW

## Findings
- [nit] src/calculator.py:14 — the docstring for `subtract` could say which argument is subtracted

## Acceptance criteria coverage
- AC-1: covered by test_subtract_positive
- AC-2: covered by test_subtract_negative, test_subtract_zero
- AC-3: covered by test_subtract_rejects_text

## Tests run (as reported by build.md)
- python3 -m pytest -q -> 12 passed
