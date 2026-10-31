-- SQLite schema for the minimal curation reference host.
-- The program creates these tables itself; this file is the documented mirror.

CREATE TABLE IF NOT EXISTS leases (
  token      TEXT PRIMARY KEY,
  owner      TEXT NOT NULL,
  purpose    TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  source_id      TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  paths_json     TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  purpose        TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'pending',
  breakpoint_json TEXT,
  updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reads (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  token      TEXT NOT NULL,
  path       TEXT NOT NULL,
  kind       TEXT NOT NULL,
  range_start INTEGER NOT NULL,
  range_end   INTEGER NOT NULL,
  sha256     TEXT NOT NULL,
  read_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
  commit_key    TEXT PRIMARY KEY,
  token         TEXT NOT NULL,
  source_id     TEXT NOT NULL,
  operation     TEXT NOT NULL,
  params_sha256 TEXT NOT NULL,
  status        TEXT NOT NULL,
  journal_json  TEXT NOT NULL,
  created_at    REAL NOT NULL,
  UNIQUE(token, params_sha256)
);
CREATE TABLE IF NOT EXISTS page_versions (
  path       TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  commit_key TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lint_reports (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  path          TEXT NOT NULL,
  range_start   INTEGER NOT NULL,
  range_end     INTEGER NOT NULL,
  coverage_kind TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
