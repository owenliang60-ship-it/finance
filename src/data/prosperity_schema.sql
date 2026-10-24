CREATE TABLE scheme (
    scheme_hash TEXT PRIMARY KEY NOT NULL,
    scheme_id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    factor_specs_json TEXT NOT NULL,
    description TEXT NOT NULL,
    changes_json TEXT NOT NULL
);
CREATE TABLE params (
    params_version TEXT PRIMARY KEY NOT NULL,
    scheme_hash TEXT NOT NULL REFERENCES scheme(scheme_hash),
    code_version TEXT NOT NULL,
    as_of TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
-- A week's input archive, stored once and shared by every scheme board and the freeze copy.
CREATE TABLE input_set (
    input_set_id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    code_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stock_count INTEGER NOT NULL CHECK(stock_count >= 0),
    UNIQUE(as_of,code_version,content_hash)
);
CREATE TABLE stock_input (
    input_set_id INTEGER NOT NULL REFERENCES input_set(input_set_id),
    symbol TEXT NOT NULL,
    input_json TEXT NOT NULL,
    factor_meta_json TEXT NOT NULL,
    PRIMARY KEY(input_set_id,symbol)
);
CREATE TABLE board (
    board_id INTEGER PRIMARY KEY,
    input_set_id INTEGER NOT NULL REFERENCES input_set(input_set_id),
    board_type TEXT NOT NULL CHECK(board_type IN ('weekly','quarter_final')),
    as_of TEXT NOT NULL,
    scheme_hash TEXT NOT NULL REFERENCES scheme(scheme_hash),
    code_version TEXT NOT NULL,
    scheme_id TEXT NOT NULL,
    params_version TEXT NOT NULL REFERENCES params(params_version),
    frozen_params_version TEXT REFERENCES params(params_version),
    quarter_label TEXT,
    frozen_at TEXT,
    computed_at TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('live','replay')),
    params_bootstrap INTEGER NOT NULL CHECK(params_bootstrap IN (0,1)),
    frozen_as_of TEXT,
    counts_json TEXT NOT NULL,
    members_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    source_evidence_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stock_count INTEGER NOT NULL CHECK(stock_count >= 0),
    factor_count INTEGER NOT NULL CHECK(factor_count >= 0),
    UNIQUE(board_type,as_of,scheme_hash,code_version)
);
CREATE INDEX board_history ON board(scheme_hash,code_version,board_type,as_of);
-- One immutable quarter-end board per quarter, scheme and code version.
CREATE UNIQUE INDEX board_quarter_final ON board(scheme_hash,code_version,quarter_label)
    WHERE board_type='quarter_final';
CREATE TABLE stock_period (
    board_id INTEGER NOT NULL REFERENCES board(board_id),
    symbol TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    industry TEXT,
    current_fiscal TEXT,
    pit_basis TEXT NOT NULL CHECK(pit_basis IN ('live','strict','approximate')),
    PRIMARY KEY(board_id,symbol)
);
CREATE TABLE factor_value (
    board_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    factor TEXT NOT NULL,
    raw_value REAL,
    raw_missing TEXT,
    scheme_value REAL,
    scheme_missing TEXT,
    z REAL,
    weight REAL,
    contribution REAL,
    in_scheme INTEGER NOT NULL CHECK(in_scheme IN (0,1)),
    raw_present INTEGER NOT NULL CHECK(raw_present IN (0,1)),
    raw_missing_present INTEGER NOT NULL CHECK(raw_missing_present IN (0,1)),
    scheme_present INTEGER NOT NULL CHECK(scheme_present IN (0,1)),
    scheme_missing_present INTEGER NOT NULL CHECK(scheme_missing_present IN (0,1)),
    PRIMARY KEY(board_id,symbol,factor),
    FOREIGN KEY(board_id,symbol) REFERENCES stock_period(board_id,symbol)
);
CREATE TABLE score (
    board_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    row_index INTEGER NOT NULL CHECK(row_index >= 0),
    status TEXT NOT NULL CHECK(status IN ('ranked','observe','excluded')),
    observe_reason TEXT,
    coverage REAL NOT NULL,
    composite REAL,
    base REAL,
    trend_adj REAL,
    score REAL,
    grade TEXT CHECK(grade IN ('STRICT','FULL','BELOW')),
    within_score REAL,
    rank INTEGER CHECK(rank > 0),
    family_scores_json TEXT NOT NULL,
    demotions_json TEXT NOT NULL,
    badges_json TEXT NOT NULL,
    PRIMARY KEY(board_id,symbol),
    UNIQUE(board_id,row_index),
    FOREIGN KEY(board_id,symbol) REFERENCES stock_period(board_id,symbol)
);
CREATE INDEX score_rank ON score(board_id,rank);
CREATE TABLE publication (
    board_id INTEGER PRIMARY KEY REFERENCES board(board_id),
    published_at TEXT NOT NULL
);
