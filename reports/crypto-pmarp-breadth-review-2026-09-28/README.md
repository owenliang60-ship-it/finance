# Review fixes — Crypto PMARP breadth

Follow-up to 105d9ac9, in the same `codex/crypto-pmarp-breadth` worktree.
Not merged, not pushed, not deployed. Cloud execution was scratch dry-run only.

## Resolution of review points

1. Daily trends and breadth execute independently for a fixed UTC cutoff;
   errors (including error-notice failures) are collected into a dated status
   JSON after both parts have run. Trend failure has its own unavailable notice.
   Successful 10d, 14d and breadth sends have individual hash-checked receipts;
   retries skip identical acknowledged messages. Changed contents under a sent
   key fail explicitly instead of falsely claiming the correction was delivered.
   The existing serial job lock is required. Telegram has no idempotency key:
   lost acknowledgments or a crash between remote send/local receipt remain an
   ambiguity, not an exactly-once guarantee.
2. Breadth now uses one rolling per-symbol price file, at most 731 bars, with
   integrity/continuity checks and atomic replacement only after full validation.
   It imports old dated snapshots without deleting them, fetches only missing
   spans, preserves valid data on failed refresh, and never rolls a newer cache
   backward for a historical replay. About 52 MB of prices are retained overall
   instead of adding about 52 MB every day; small dated reports/catalog evidence
   still grow. Per-symbol API calls remain, so daily elapsed time is not seconds.
3. Complete cached retired history is reused with no REST request. If required
   historical prices or lifecycle evidence are genuinely missing, the report
   remains unavailable until repaired. Existing cached trading history prevents
   a pending-status error from misclassifying a contract as never opened.
4. Default retired price files are read directly from their versioned bundled
   paths; no manual copy into production output is needed. Startup checks all
   file presence/hashes and aggregates errors before any API collection.
5. Derived supplements are no longer saved as observed exchange snapshots.
   Legacy source-tagged derived rows are ignored on load and today's manifest
   is applied again. Genuine exchange observations still take precedence.
6. Contracts retiring before the first comparison day's close are excluded
   before fetching; they cannot block a report in which they never participate.
7. Per-symbol fetch/calculation failures are collected across all candidates.
8. The agreed right-inclusive percentile formula is retained. The misleading
   monotonic prevalence sentence is removed; raw breadth is emphasized and
   the zero/tie explanation remains. Midrank would be a separate methodology
   change, not bundled into this engineering fix.

The duplicated unopened-contract proof is now one shared helper. The small
percentile calculation was not moved across unrelated valuation modules merely
for cosmetic deduplication.

## Evidence

- `python -m pytest tests/test_crypto* -q`: **319 passed in 8.81s**.
- `python -m pytest tests -q --tb=short`: **4339 passed, 4 skipped, 12 failed**
  in 275.40s. Failure IDs exactly match the original full-suite 12 failures
  (non-Crypto, research fixtures/morning report). See `fullsuite_comparison.json`;
  no new full-suite failure was introduced. The full suite is not green.
- Python 3.10 syntax and `git diff --check`: PASS.
- One independent review agent found two further P2s (changed-content receipts
  and existing-history/pending errors); both were reproduced, fixed, and
  independently rechecked, 2 targeted regressions passing, no remaining findings.
- Frozen real data replay: 610 contracts, all 366 daily counts, current per-coin
  PMARP and both 365-day percentiles are identical after legacy import and
  rolling-cache replay. Network forbidden during these checks.
- Real next-day cache exercise: 525 requests, exactly 1 new bar each; 85 retired
  contracts reused without downloading. This uses real frozen bars with a
  fake transport, not an assertion about observed live endpoint latency.
- Python 3.10 cloud scratch dry-run: exit 0; same 506 valid contracts, 70 strong,
  1 weak, P94.52054794520548/P7.123287671232877. No Telegram sends occurred.

The original acceptance report and raw snapshots remain historical evidence.
Current preview wording is in `message-preview.md`. The current operational
contract is `docs/runbooks/crypto-pmarp-breadth.md`.
