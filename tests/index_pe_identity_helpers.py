"""Explicit synthetic identity fixtures; never used on real trial responses."""
import hashlib
import json


def synthetic_lei(label):
    body = "TEST" + str(int(hashlib.sha256(label.encode()).hexdigest(), 16) % 10**14).zfill(14)
    numeric = "".join(c if c.isdigit() else str(ord(c) - 55) for c in body + "00")
    return body + str(98 - int(numeric) % 97).zfill(2)


def seed_source_identity(conn, where="1=1", params=(), issuer_labels=None):
    """Attach full synthetic source evidence when creating a toy snapshot.

    Call explicitly at fixture creation, never from an autouse fixture or the
    verifier: a test deleting evidence must continue to fail.
    """
    cursor = conn.execute(f"SELECT rowid AS source_rowid, * FROM fmp_fund_disclosure_holdings WHERE {where}", params)
    names = [c[0] for c in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    for row in rows:
        symbol = row["raw_symbol"] or row["symbol"] or row.get("covered_by")
        label = (issuer_labels or {}).get(symbol, row.get("covered_by") or symbol)
        lei = synthetic_lei(label)
        cusip = str(int(hashlib.sha256(symbol.encode()).hexdigest(), 16) % 10**9).zfill(9)
        isin = "US" + cusip + "0"
        raw = {"lei": lei, "cusip": cusip, "isin": isin, "assetCat": "EC",
               "symbol": symbol, "asset": symbol, "date": row["holding_date"],
               "cik": "0000884394", "acceptedDate": row["composition_available_date"]}
        conn.execute("UPDATE fmp_fund_disclosure_holdings SET issuer_lei=?,cusip=?,isin=?,"
                     "asset_category='EC',raw_payload_json=?,cik='0000884394' WHERE rowid=?",
                     [lei, cusip, isin, json.dumps(raw), row["source_rowid"]])
