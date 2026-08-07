CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS market_ticks (
  id bigserial,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  instrument text NOT NULL,
  bid double precision NOT NULL,
  ask double precision NOT NULL,
  source text NOT NULL,
  source_sequence text NOT NULL DEFAULT '',
  CONSTRAINT uq_market_tick_source UNIQUE (instrument, occurred_at, source, source_sequence)
);
SELECT create_hypertable('market_ticks', by_range('occurred_at'), if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS event_envelopes (
  event_id uuid PRIMARY KEY,
  schema_version integer NOT NULL,
  kind text NOT NULL,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  source text NOT NULL,
  source_sequence text,
  idempotency_key text NOT NULL UNIQUE,
  payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_manifests (
  dataset_id text PRIMARY KEY,
  created_at timestamptz NOT NULL,
  source_file text NOT NULL,
  sha256 text NOT NULL UNIQUE,
  row_count bigint NOT NULL,
  first_timestamp timestamptz,
  last_timestamp timestamptz,
  issue_counts jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS news_events (
  event_id uuid PRIMARY KEY,
  occurred_at timestamptz NOT NULL,
  published_at timestamptz NOT NULL,
  source text NOT NULL,
  event_type text NOT NULL,
  headline text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS movement_cases (
  case_id uuid PRIMARY KEY,
  instrument text NOT NULL,
  window_started_at timestamptz NOT NULL,
  window_ended_at timestamptz NOT NULL,
  return_bps double precision NOT NULL,
  primary_cause text,
  attribution_confidence double precision NOT NULL,
  hypotheses jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_journal (
  decision_id uuid PRIMARY KEY,
  recorded_at timestamptz NOT NULL,
  instrument text NOT NULL,
  payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
  experiment_id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  git_sha text NOT NULL,
  dataset_version text NOT NULL,
  config jsonb NOT NULL,
  metrics jsonb NOT NULL
);
