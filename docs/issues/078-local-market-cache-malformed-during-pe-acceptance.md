# Issue 078: Local market.db cache was malformed during PE acceptance

- Date: 2026-09-12
- Status: local cache restored from the cloud authority; corruption cause unresolved
- Scope: local data mirror, not a production valuation/calculation failure

Full tests first returned12 failures: five missing ignored breadth CSV tests,
and seven registry/compass failures associated with local SQLite
`database disk image is malformed`. The main cache mtime was09:02, and the
feature worktree's existing market.db symlink pointed to it. This establishes
the observed state, not which writer/sync operation caused the damage.

The same tests pass with an isolated copy of a verified cloud backup:
187 passed/1 skipped, then full3566 passed/4 skipped. No production data was
changed to satisfy tests, and no relevant test was skipped or relaxed.

After the PE production transaction committed and external validation passed,
a read-only SQLite backup of cloud market.db was downloaded. Both ends and
the installed local cache have SHA256
`440ad396050d6330bd0af509ac8eaacfd1894ee0e06221b663285316602d19f6`.
The local sync interlock, two open-handle checks, zero-length WAL check,
byte-preserving archive, atomic replacement, quick_check and773/60 product
counts all passed. Local company.db and other owned assets were untouched.

Evidence: rollout artifact `local-mirror-install.json` and
`local-cache-before-refresh/`; retained healthy `published-market.db`.
Do not infer that the existing scheduled pull is now hardened: this was a
bounded cache restoration, not a rewrite or root-cause fix for that script.
