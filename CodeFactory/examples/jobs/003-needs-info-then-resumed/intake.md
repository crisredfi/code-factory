DECISION: ACCEPT
RISK: LOW
SCOPE: small
REVIEWERS_ADD: none
REVIEWERS_DROP: architecture

## Summary
- Answer received: Markdown only, trash excluded.
- Small and contained: one button handler and one exporter.
- Low risk: writes a local file the user chose.

## Scope
Make the export button write each visible note to a Markdown file. Notes in the trash are not exported. PDF is out of scope.

## Reviewers
- architecture dropped: no new module boundary.

## Why
The export handler exists but its exporter returns early when no format is set. Setting the format to Markdown is the fix; the answer confirms it.
