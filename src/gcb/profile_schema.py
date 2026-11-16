"""Additive schema 1 -> 2 migration, preserving every G1 identity and research row."""
PROFILE_COLUMNS = {
    "profile_metadata": ("id", "model_id", "body_json", "checked_at"),
    "profile_plans": ("id", "model_id", "body_json", "status", "created_at", "not_before", "error_code", "elapsed_seconds"),
    "profile_grants": ("id", "plan_id", "plan_digest", "expires_at", "max_requests"),
    "profile_cells": ("id", "configuration_id", "case_id", "status", "observation_json"),
    "profile_plan_cells": ("id", "plan_id", "cell_id", "position"),
    "profile_attempts": ("id", "plan_id", "cell_id", "status", "started_at", "ended_at", "summary_json"),
    "profile_worker": ("id", "owner", "expires_at"),
}
PROFILE_REFERENCES = {
    "profile_metadata": {("model_id", "models", "id")},
    "profile_plans": {("model_id", "models", "id")},
    "profile_grants": {("plan_id", "profile_plans", "id")},
    "profile_cells": {("configuration_id", "configurations", "id")},
    "profile_plan_cells": {("plan_id", "profile_plans", "id"), ("cell_id", "profile_cells", "id")},
    "profile_attempts": {("plan_id", "profile_plans", "id"), ("cell_id", "profile_cells", "id")},
    "profile_worker": set(),
}
MIGRATION = """
BEGIN IMMEDIATE;
CREATE TABLE profile_metadata(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id), body_json TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE profile_plans(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id), body_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, not_before TEXT NOT NULL, error_code TEXT NOT NULL, elapsed_seconds TEXT NOT NULL);
CREATE TABLE profile_grants(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES profile_plans(id), plan_digest TEXT NOT NULL, expires_at TEXT NOT NULL, max_requests TEXT NOT NULL);
CREATE TABLE profile_cells(id TEXT PRIMARY KEY, configuration_id TEXT NOT NULL REFERENCES configurations(id), case_id TEXT NOT NULL, status TEXT NOT NULL, observation_json TEXT NOT NULL);
CREATE TABLE profile_plan_cells(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES profile_plans(id), cell_id TEXT NOT NULL REFERENCES profile_cells(id), position TEXT NOT NULL);
CREATE TABLE profile_attempts(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES profile_plans(id), cell_id TEXT NOT NULL REFERENCES profile_cells(id), status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL, summary_json TEXT NOT NULL);
CREATE TABLE profile_worker(id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE INDEX profile_attempt_plan ON profile_attempts(plan_id,cell_id);
PRAGMA user_version=2;
COMMIT;
"""
