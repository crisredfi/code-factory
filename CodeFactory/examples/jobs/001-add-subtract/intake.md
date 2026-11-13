DECISION: ACCEPT
RISK: LOW
SCOPE: small
REVIEWERS_ADD: none
REVIEWERS_DROP: architecture

## Summary
- Adds one pure function next to `add`, with tests.
- No new modules, no public API change beyond the new function.
- Low risk: contained, easy to verify, nothing sensitive.

## Scope
Add `subtract(a, b)` to the calculator module. Tests for positive, negative and zero inputs.
Out of scope: division, input parsing, a CLI.

## Reviewers
- architecture dropped: a single function in an existing module adds no new boundary.

## Why
`src/calculator.py` already has `add` with the same signature style. Nothing else touches it.
