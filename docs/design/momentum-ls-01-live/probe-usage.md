# Demo protocol probe harness

2026-10-20. **Harness implemented; no live demo probe has been run.** Unit-test fixtures are not protocol evidence. Task 1.4 and gates G1–G5 remain open.

## Invocation contract

The CLI calls:

```python
run_probe(
    group, client, output_dir,
    symbol='...', quantity='...',
    trigger_price=None, replacement_trigger_price=None,
    second_symbol=None, second_quantity=None,
    worker_factory=None,
)
```

- `group`: `g1` through `g5`.
- `client`: `BinanceClient(env='demo', credentials=load_credentials(...), allow_demo_writes=True)`.
- `symbol` and `quantity`: explicit test inputs. There is no automatic trade sizing.
- G1/G2/G3 require `trigger_price`; G2 also requires `replacement_trigger_price`. Both must be above current contract price for these bounded short-position experiments. Prices round upward to the symbol tick.
- G5 requires a distinct `second_symbol` and `second_quantity`. The first symbol is short, the second long.
- `worker_factory` is an offline-test injection. Default G5 workers have separate HTTP transports and share the rate limiter and credentials.
- `output_dir`: evidence root. A fresh UUID subdirectory is created for every run. The function returns the final evidence dictionary.

The invoking CLI controls whether to enable demo writes. Production is rejected before any read or write. The harness records a SHA256 fingerprint of the selected credential automatically; this binds the evidence to that key, not to an exchange UID. It does not verify dedicated-account ownership or key permissions.

## Bounded experiments

| Group | Implemented experiment | Still requires separate observation / implementation |
|---|---|---|
| G1 | Open a short; submit quantity-based reduceOnly STOP_MARKET; verify side, quantity, trigger, contract-price working type, priceProtect=false and closePosition=false | Natural trigger, reversal prevention at trigger, crossed-price rejection and corresponding exit |
| G2 | Open a short; old/new stops coexist; confirm both IDs before canceling old | Run both higher and lower replacement choices; old trigger during replacement; rejected-new rollback; shared-cause rejection |
| G3 | Open a short; protect it; partially reduce via ordinary reduceOnly MARKET; confirm old stop remains active | Partial opening/stop fills, ADL, excess-quantity trigger behavior, automatic cancellation at zero |
| G4 | Set/read 2x crossed configuration; equal-size open/add; compare entryPrice to execution-price average; partially reduce; confirm unchanged basis; capture income response | Distinct execution prices when the market does not move; funding/commission interpretation; process restart; full close/reopen; key permissions |
| G5 | Paired short/long seed; concurrent reduction wave A; verify terminal fills; read available balance; concurrent addition wave B | Five-slot saturation; partial/rejected/unknown requests; D6 shortage scaling. Insufficient observed margin stops wave B and marks BLOCKED |

Each full group returns `PARTIAL`, `FAIL` or `BLOCKED`, never `PASS`. Individual checks may pass. `remaining_matrix` explicitly records unobserved scenarios. The harness does not wait for market movement, manufacture ADL or funding events, or assert real exchange behavior from mocks.

## Preflight and mutation boundary

Before opening any position, the harness reads current exchange filters, prices, account modes, positions and both order families. It requires:

- demo environment, one-way position mode, single-asset margin;
- no existing nonzero position anywhere in the account;
- no existing ordinary or conditional orders;
- an explicit zero row for each selected symbol;
- eligible contracts and valid explicit quantities;
- 2x crossed configuration read back after setup.

Position snapshots use `/fapi/v2/positionRisk` for explicit zero rows. V3 omits symbols without positions/open orders, so absence cannot certify zero. This distinction is documented in Binance's [official position reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade).

Preexisting holdings/orders cause a read-only stop. The probe never adopts them. Symbol leverage/margin settings changed during preflight remain at the required 2x crossed configuration after the run.

## Evidence and crash behavior

Every mutation first writes its purpose, method, path, exact non-signing parameters, unique client identity and `SENDING` status. Persistence uses temporary-file replacement plus file/directory fsync. G5 registers the entire wave before any worker starts.

A successful response is recorded as ACKED, then queried by identity. Unknown writes are recorded UNKNOWN and never resent. A crash can leave SENDING; that entry remains a recovery obligation. Disk failures stop further writes. The harness intentionally has no automatic interrupted-run replay. Reconcile every outstanding client ID before approving another run; a run with `RUNNING`, SENDING/UNKNOWN requests, or blocked cleanup requires manual review.

Evidence includes reads, write outcomes, check statuses and cleanup results. Credential values, signatures and key-like fields are removed. No raw exception text is stored. No synthetic fixture output is saved under the real evidence directory by unit tests.

## Cleanup limits

Cleanup first resolves probe-owned ordinary identities. It never infers nonacceptance from one not-found reply. It queries each owned conditional parent and its `actualOrderId` child separately. A nonterminal/unknown child blocks additional market closes.

The harness sums confirmed owned executions, checks recent trades for foreign order IDs, and compares those totals to actual positions before cleanup writes. Open conditional orders are allowed only when they exactly match the still-active owned IDs. Parent state is checked again immediately before closing; a trigger during attribution blocks any additional close. Only an exact attributable residual may be closed with reduceOnly MARKET. Active owned protection stays in place until that close and explicit zero positions are confirmed. Then residual owned conditional orders are individually canceled and confirmed terminal. There is no cancel-all. A rejected, conflicting or unknown close preserves protection and returns BLOCKED; the harness never cancels protection to force a close.

Unresolved requests, foreign trades, mismatched positions, incomplete trade history, missing zero rows, failed cancellation or failed close produce `cleanup.status=BLOCKED`. The output identifies the need for manual reconciliation. Cleanup is bounded and does not loop indefinitely or retry unknown writes. This is a demo test harness, not the production protection controller.

## Verification

Offline tests exercise preexisting-account refusal, durable intents, crash retention, no duplicate opening after lost response, unresolved-effect refusal, parent/child distinction, foreign-trade refusal, quantity protection, replacement, reduction, cost checks and paired-wave concurrency. The concurrency test also confirms both intents exist durably before either network mutation starts.

Real G1–G5 evidence and the full matrix remain prerequisites for stage 2 approval. Passing harness tests does not satisfy those gates.
