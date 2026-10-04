CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    path_hash TEXT NOT NULL,
    display_alias TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_offset INTEGER NOT NULL,
    last_event_hash TEXT,
    scan_status TEXT NOT NULL,
    UNIQUE(source_type, path_hash)
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_path_hash TEXT NOT NULL,
    display_alias TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(source, workspace_path_hash)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    workspace_id INTEGER,
    started_at TEXT,
    ended_at TEXT,
    producer_version TEXT,
    PRIMARY KEY(session_id, source),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    root TEXT NOT NULL,
    result TEXT NOT NULL,
    warnings_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    shutdown_timestamp TEXT,
    task_manifest_id INTEGER,
    run_selection_policy TEXT NOT NULL,
    is_delta INTEGER NOT NULL,
    scan_id INTEGER NOT NULL,
    UNIQUE(session_id, source, run_kind, run_selection_policy, shutdown_timestamp),
    FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
);



CREATE TABLE IF NOT EXISTS usage_records (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL,
    record_key TEXT NOT NULL,
    request_id TEXT,
    timestamp TEXT NOT NULL,
    model_key TEXT NOT NULL,
    event_order INTEGER NOT NULL DEFAULT 0,
    raw_input_tokens INTEGER NOT NULL,
    fresh_input_tokens INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    context_window_tokens INTEGER,
    reported_ai_credits REAL,
    ai_credits_source TEXT NOT NULL DEFAULT 'credits_unavailable',
    reported_nano_aiu INTEGER,
    observed_nano_aiu INTEGER,
    inclusion_status TEXT,
    history_scope TEXT,
    match_key TEXT,
    initiator TEXT,
    evidence_error TEXT,
    tokens_valid INTEGER NOT NULL DEFAULT 1,
    reasoning_tokens_valid INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_file_id, model_key, record_key),
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(source_file_id) REFERENCES source_files(source_id)
);

CREATE TABLE IF NOT EXISTS diagnostics (
    diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    source_file_id INTEGER,
    line_number INTEGER,
    session_id TEXT,
    history_scope TEXT,
    diagnostic_code TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id),
    FOREIGN KEY(source_file_id) REFERENCES source_files(source_id)
);

CREATE TABLE IF NOT EXISTS session_aggregates (
    aggregate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    source_file_id INTEGER NOT NULL REFERENCES source_files(source_id),
    timestamp TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    reported_nano_aiu INTEGER,
    observed_nano_aiu INTEGER,
    inclusion_status TEXT,
    history_scope TEXT,
    match_key TEXT,
    initiator TEXT,
    evidence_error TEXT,
    tokens_valid INTEGER NOT NULL DEFAULT 1,
    reasoning_tokens_valid INTEGER NOT NULL DEFAULT 1,
    ai_credits_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    estimated_primary_model TEXT,
    request_id TEXT,
    UNIQUE(source_file_id, event_order, ai_credits_source)
);

CREATE TABLE IF NOT EXISTS cli_history_inputs (
    scope TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_files(source_id),
    error TEXT,
    PRIMARY KEY(scope, session_id)
);
