"""Certify the isolated trial only. Never opens the production database."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.verify_index_pe_history import connect_readonly, verify_database
from scripts.verify_fmp_forward import verify_run
from scripts.update_fmp_forward import parse_args, run_valuation_phase

ARTIFACTS = Path(__file__).resolve().parent
DB = ARTIFACTS / "recovery.db"
DAY = "2026-10-24"


def save(name, value):
    (ARTIFACTS / name).write_text(json.dumps(value, indent=2, default=str) + "\n")


def source_fingerprint(conn, table, excluded=()):
    digest, count = hashlib.sha256(), 0
    for row in conn.execute("SELECT * FROM " + table + " ORDER BY symbol,date"):
        if (row['symbol'], row['date']) in excluded:
            continue
        digest.update(json.dumps(dict(row), sort_keys=True, separators=(',', ':')).encode())
        digest.update(b'\n')
        count += 1
    return {'rows':count, 'sha256':digest.hexdigest()}


def history(name):
    conn = connect_readonly(DB)
    try:
        result = verify_database(conn, ["SPY", "QQQ", "SOXX"], DAY, 5, sample=50)
    finally:
        conn.close()
    save(name, result)
    if not result["passed"]:
        raise RuntimeError("history certification failed: " + name)


def main():
    assert DB.resolve().parent == ARTIFACTS
    assert DB.is_file()
    completion = json.loads((ARTIFACTS / "recovery-spy-history.json").read_text())
    assert completion["failed_baskets"] == []
    history("trial-history-before-pit.json")
    print("three history baskets certified", flush=True)

    data = ARTIFACTS / "recovery-data"
    data.mkdir(exist_ok=True)
    if not (data / "market.db").exists():
        (data / "market.db").symlink_to(DB)
    assert (data / "market.db").resolve() == DB.resolve()
    args = parse_args(["--mode", "weekly", "--phase", "valuation", "--snapshot-date", DAY,
                       "--data-root", str(data)])
    rc, result = run_valuation_phase(args)
    save("trial-pit-valuation.json", result)
    if rc:
        raise RuntimeError("PIT valuation failed")
    print("six-basket PIT valuation committed in isolated copy", flush=True)
    rc, result = verify_run(DB, data, DAY, stage="full")
    save("trial-pit-verifier.json", result)
    if rc:
        raise RuntimeError("full PIT verification failed")
    history("trial-history-final.json")

    conn = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    baseline = sqlite3.connect((ARTIFACTS / "baseline.db").as_uri() + "?mode=ro", uri=True)
    baseline.row_factory = sqlite3.Row
    original = [dict(r) for r in baseline.execute("SELECT * FROM fundamental_vintage WHERE symbol='CRH'")]
    for r in original:
        got = conn.execute("SELECT * FROM fundamental_vintage WHERE symbol=? AND statement=? AND fiscal_date=? AND observed_at=?",
                           (r['symbol'], r['statement'], r['fiscal_date'], r['observed_at'])).fetchone()
        assert got is not None and dict(got) == r
    assert conn.execute("SELECT count(*) FROM fundamental_vintage WHERE symbol='CRH'").fetchone()[0] == len(original)
    rules = json.loads((ROOT / "config/reviewed_statement_quarantines.json").read_text())
    for rule in rules:
        assert conn.execute("SELECT count(*) FROM " + rule['table'] + " WHERE symbol=? AND date=?",
                            (rule['symbol'], rule['date'])).fetchone()[0] == 0
    archived = conn.execute("SELECT count(*) FROM fundamental_current_archive WHERE reason='reviewed_source_quarantine'").fetchone()[0]
    assert archived == 5
    source_comparison = {}
    for table in ('income_quarterly', 'balance_sheet_quarterly', 'cash_flow_quarterly'):
        excluded = {(r['symbol'], r['date']) for r in rules if r['table'] == table}
        expected = source_fingerprint(baseline, table, excluded)
        actual = source_fingerprint(conn, table)
        assert actual == expected, 'unexpected current source mutation: ' + table
        source_comparison[table] = actual
    plans = json.loads((ROOT / 'docs/audit/cron-recovery-20261024/quarantine-plan.json').read_text())
    for plan in plans:
        old = dict(baseline.execute('SELECT * FROM '+plan['table']+' WHERE symbol=? AND date=?',
                                   (plan['symbol'], plan['date'])).fetchone())
        proof = conn.execute("SELECT payload,context FROM fundamental_current_archive WHERE reason='reviewed_source_quarantine' AND source_table=? AND symbol=? AND original_date=?",
                             (plan['table'], plan['symbol'], plan['date'])).fetchall()
        assert len(proof) == 1 and json.loads(proof[0]['payload']) == old
        assert json.loads(proof[0]['context'])['plan'] == plan
    cutoff = baseline.execute('SELECT coalesce(max(archive_id),0) FROM fundamental_current_archive').fetchone()[0]
    reasons = {r[0] for r in conn.execute('SELECT distinct reason FROM fundamental_current_archive WHERE archive_id>?', (cutoff,))}
    assert reasons <= {'reviewed_source_quarantine', 'metrics_recomputed'}
    result = {"status":"complete", "history_api_requests":0, "quarantined_current_rows":5,
              "original_vintage_rows_unchanged":len(original), "archived_rows":archived,
              "all_current_statements_equal_baseline_minus_five":source_comparison,
              "quick_check":conn.execute("PRAGMA quick_check").fetchone()[0],
              "history":[dict(r) for r in conn.execute("SELECT basket,count(*) rows,min(valuation_date) first,max(valuation_date) latest FROM basket_weekly_pe_history GROUP BY basket")],
              "consensus":[dict(r) for r in conn.execute("SELECT basket,max(snapshot_date) latest FROM fmp_basket_valuation GROUP BY basket")]}
    assert result['quick_check'] == 'ok'
    baseline.close();conn.close()
    digest = hashlib.sha256()
    with (ARTIFACTS / 'baseline.db').open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    assert digest.hexdigest() == json.loads((ARTIFACTS / 'receipt.json').read_text())['sha256']
    result['baseline_sha256_unchanged'] = digest.hexdigest()
    save('trial-summary.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
