STATUS: SPLIT
DRAFT_PR: none
BRANCH: factory/issue-20
ROUND: 1

## Why split
Four parts with different reviewers: storage (security, code), sync (security, code), conflicts (code, architecture), and the toolbar (ux, code). Reviewing them together would make each finding harder to place.

## Proposed sub-issues
See build/split.json. Dependencies: c2 waits for c1, c3 and c4 wait for c2.

## Notes for the maintainer
- Nothing is created on GitHub until you approve the split.
