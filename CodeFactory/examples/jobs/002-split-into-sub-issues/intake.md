DECISION: ACCEPT
RISK: MEDIUM
SCOPE: large
REVIEWERS_ADD: ux
REVIEWERS_DROP: none

## Summary
- Large: local storage, a sync engine, conflict handling and UI each need their own review.
- Medium risk: changes how data is stored and merged. A bug here loses notes.
- Accepted, but the builder should propose a split rather than build it in one pull request.

## Scope
Offline create and edit, background sync when online, a conflict rule (last write wins per field, with the other version kept), and a sync status in the toolbar. Out of scope: shared editing between users.

## Reviewers
- ux added: the sync status and the conflict message are user-facing.

## Why
The notes store is a single file-backed repository today. Nothing syncs, so the change adds a new layer.
