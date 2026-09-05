# 059 — Live SQLite rsync can publish an inconsistent local snapshot

Date: 2026-09-05

Status: local snapshot recovered and verified; sync helper hardening is a separate follow-up.

## Evidence

After the fiscal-alias repair, production quick_check and the actual compass
scan passed. The subsequent standard `sync_to_cloud.sh --pull` ran during the
Saturday weekly refresh window. A local read of historical_market_cap later
raised `sqlite3.DatabaseError: database disk image is malformed`.

Independent cloud recheck returned quick_check=ok and a readable 3,869,121-row
HMC table. Local WAL was zero bytes and no process had the local DB open.
The evidence localizes this incident to the downloaded copy, not production.

The pull helper checkpoints the cloud WAL and then rsyncs the live main DB;
that does not freeze the file against subsequent writes. Concurrent maintenance
can change pages while they are transferred. This is the leading explanation,
not a claim that the exact conflicting write has been identified.

## Recovery and required follow-up

Create a consistent cloud snapshot using SQLite backup API, verify it, transfer
that immutable snapshot with checksum verification, then atomically publish it
locally after confirming no local DB handles remain. Preserve the failed local
copy and its sidecars for investigation.

Durable follow-up: change the pull helper to export an immutable SQLite snapshot
before transfer, validate the downloaded file before publication and avoid
combining a replacement DB with stale local WAL sidecars. A WAL checkpoint alone
must not be treated as a transferable snapshot guarantee.

Recovery completed: SQLite backup snapshot
`/tmp/finance-fiscal-consistent-snapshot-20260905.db` on aliyun, 1,016,463,360 bytes,
SHA256 `e0fe41b65d7e75586c7af4cd46b485b916cbe6cbbb0ee02ecc435f41ebc4ed15`.
Downloaded staging file matched that hash and passed quick_check. After verifying
there were no open local DB handles, the failed local DB and its sidecars were
preserved as `data/market.db.failed-pull-20260905` and corresponding sidecar
archives, and the verified snapshot was atomically published. Published
quick_check=ok, HMC reads succeed, and all 85 fiscal-repair archive rows remain.
