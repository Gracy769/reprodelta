"""Privacy-limited operator aggregates for the approved-project pilot."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from app.storage.project_profiles import ProjectProfileStore, _dsn
from app.storage.project_run_queue import ProjectRunQueue


class PilotMetricsStore:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    def read(self) -> dict:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as conn:
            ProjectProfileStore._ensure(conn)
            ProjectRunQueue._ensure(conn)
            profiles = conn.execute("""SELECT
                count(*) AS created,
                count(*) FILTER (WHERE status='approved') AS approved,
                count(DISTINCT owner_id) AS owners_started,
                count(DISTINCT owner_id) FILTER (WHERE status='approved') AS owners_approved
                FROM project_profile_versions""").fetchone()
            runs = conn.execute("""SELECT
                count(*) AS started,
                count(*) FILTER (WHERE status='queued') AS queued,
                count(*) FILTER (WHERE status='running') AS running,
                count(*) FILTER (WHERE status='completed') AS completed,
                count(*) FILTER (WHERE status='failed') AS failed,
                count(*) FILTER (WHERE status='cancelled') AS cancelled,
                count(*) FILTER (WHERE status='completed'
                    AND result_json->>'changed_conclusion'='true') AS changed_outcome,
                count(*) FILTER (WHERE status='completed'
                    AND result_json->>'inconclusive'='true') AS inconclusive,
                count(*) FILTER (WHERE usage_uncertain) AS uncertain_cost_runs,
                coalesce(sum(estimated_sandbox_cost_usd), 0) AS estimated_sandbox_usd,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY extract(epoch FROM finished_at-created_at))
                    FILTER (WHERE status='completed' AND finished_at IS NOT NULL)
                    AS median_seconds_to_result
                FROM project_run_jobs
                WHERE job_kind IN ('investigation', 'investigation:oracle_docker')""").fetchone()
            failures = conn.execute("""SELECT error_code, count(*) AS count
                FROM project_run_jobs
                WHERE job_kind IN ('investigation', 'investigation:oracle_docker')
                    AND status='failed' AND error_code IS NOT NULL
                GROUP BY error_code ORDER BY count(*) DESC, error_code LIMIT 10""").fetchall()
            return {
                "profiles": dict(profiles),
                "investigations": dict(runs),
                "failure_codes": [dict(row) for row in failures],
                "scope": "All approved-project pilot records; no owner or repository identities",
                "limitations": [
                    "Cost excludes model, source-host and VM fixed costs; uncertain runs are counted separately.",
                    "An observed change is not a verified mechanism or repair.",
                    "Unsupported eligibility checks are not persisted and cannot be counted here.",
                ],
            }
