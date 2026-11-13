# Example comments the factory posts

Each block below is exactly what `scripts/cf/notify.py` posts for one move in `docs/COMMENTS.md`.
The marker on the first line is what the factory uses to recognise its own comments.

---

## accepted (issue)

```
<!-- factory -->
### Factory: accepted

- Adds one pure function next to `add`, with tests.
- No new modules, no public API change beyond the new function.
- Low risk: contained, easy to verify, nothing sensitive.

- **Scope:** small
- **Risk:** LOW
- **Reviewers:** code, security

<sub>issue-12 · SPEC · round 1 of 3 · scope small · risk LOW</sub>
```

## needs more information (issue)

```
<!-- factory -->
### Factory: needs more information

1. Export to which format: Markdown, PDF, or both?
2. Should the export include notes in the trash?

Reply on this issue and the factory will pick it up again.

<sub>issue-31 · NEEDS_INFO · scope small · risk LOW</sub>
```

## needs a human (PR)

```
<!-- factory -->
### Factory: needs a human

Review round 2 still asks for changes to the retry logic, and round 3 is the last allowed round.

Next: merge the PR, send feedback from the dashboard, or reject the job.

<sub>issue-14 · NEEDS_HUMAN · round 3 of 3 · risk MEDIUM</sub>
```

## ready to merge (PR)

```
<!-- factory -->
### Factory: ready to merge

- **Risk:** LOW
- **Reviewers:**
  - code
  - security

A maintainer merges this PR, or auto-merge does, depending on the factory settings.

<sub>issue-12 · MERGE_PENDING · round 1 of 3 · scope small · risk LOW</sub>
```

## merged (PR)

```
<!-- factory -->
### Factory: merged

Merged by the factory.

<sub>issue-12 · COMPLETE · round 1 of 3 · scope small · risk LOW</sub>
```

## sub-issues created (parent issue)

```
<!-- factory -->
### Factory: sub-issues created

These sub-issues were created and will run through the factory:

- #21
- #22
- #23
- #24

<sub>issue-20 · WAITING_CHILDREN · round 1 of 3 · scope large · risk MEDIUM</sub>
```
