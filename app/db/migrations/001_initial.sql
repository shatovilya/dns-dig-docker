CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS historical_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    test_id TEXT NOT NULL,
    test_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    summary JSONB NOT NULL DEFAULT '{}',
    panels JSONB NOT NULL DEFAULT '{}',
    payload_size_bytes INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_historical_snapshots_created_at
    ON historical_snapshots (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_historical_snapshots_test_id
    ON historical_snapshots (test_id);
CREATE INDEX IF NOT EXISTS idx_historical_snapshots_time_range
    ON historical_snapshots (started_at, finished_at);

CREATE TABLE IF NOT EXISTS test_runs (
    snapshot_id TEXT PRIMARY KEY REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    test_id TEXT NOT NULL,
    test_name TEXT NOT NULL,
    status TEXT,
    mode TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_test_runs_started_at ON test_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_test_runs_test_id ON test_runs (test_id);

CREATE TABLE IF NOT EXISTS run_aggregates (
    snapshot_id TEXT PRIMARY KEY REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    total_queries BIGINT,
    success_rate DOUBLE PRECISION,
    error_rate DOUBLE PRECISION,
    nxdomain_rate DOUBLE PRECISION,
    noisy_ratio DOUBLE PRECISION,
    cache_hit_ratio DOUBLE PRECISION,
    latency_p50_ms DOUBLE PRECISION,
    latency_p95_ms DOUBLE PRECISION,
    latency_p99_ms DOUBLE PRECISION,
    sample_count BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resolver_aggregates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    resolver TEXT NOT NULL,
    availability DOUBLE PRECISION,
    latency_p50_ms DOUBLE PRECISION,
    latency_p95_ms DOUBLE PRECISION,
    error_count BIGINT,
    cache_efficiency DOUBLE PRECISION,
    edns_counters JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_resolver_aggregates_snapshot
    ON resolver_aggregates (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_resolver_aggregates_resolver
    ON resolver_aggregates (resolver);

CREATE TABLE IF NOT EXISTS domain_aggregates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    fqdn TEXT NOT NULL,
    latency_p50_ms DOUBLE PRECISION,
    latency_p95_ms DOUBLE PRECISION,
    error_count BIGINT,
    response_class TEXT,
    noisy_markers JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_domain_aggregates_snapshot ON domain_aggregates (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_domain_aggregates_fqdn ON domain_aggregates (fqdn);

CREATE TABLE IF NOT EXISTS error_aggregates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    error_type TEXT NOT NULL,
    count BIGINT NOT NULL,
    resolver TEXT,
    domain TEXT,
    bucket_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_error_aggregates_snapshot ON error_aggregates (snapshot_id);

CREATE TABLE IF NOT EXISTS edns_aggregates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    edns_level TEXT NOT NULL,
    query_count BIGINT,
    error_count BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, edns_level)
);

CREATE TABLE IF NOT EXISTS mtr_runs (
    run_id TEXT PRIMARY KEY,
    snapshot_id TEXT REFERENCES historical_snapshots (snapshot_id) ON DELETE SET NULL,
    test_id TEXT,
    target TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    packet_loss_summary DOUBLE PRECISION,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    hops_snapshot JSONB NOT NULL DEFAULT '[]',
    status TEXT,
    raw_report TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mtr_runs_started_at ON mtr_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_mtr_runs_snapshot ON mtr_runs (snapshot_id);

CREATE TABLE IF NOT EXISTS chart_buckets (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES historical_snapshots (snapshot_id) ON DELETE CASCADE,
    panel TEXT NOT NULL,
    bucket_at TIMESTAMPTZ NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chart_buckets_bucket_at ON chart_buckets (bucket_at DESC);
CREATE INDEX IF NOT EXISTS idx_chart_buckets_snapshot ON chart_buckets (snapshot_id);
