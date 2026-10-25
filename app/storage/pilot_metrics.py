"""Privacy-limited operator aggregates for the approved-project pilot."""

from __future__ import annotations

import hashlib
import uuid

import psycopg
from psycopg.rows import dict_row

from app.storage.project_profiles import ProjectProfileStore, _dsn
from app.storage.project_run_queue import ProjectRunQueue


class PilotMetricsStore:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    @staticmethod
    def _ensure_eligibility(conn) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_eligibility_checks (
            profile_id UUID NOT NULL REFERENCES project_profile_versions(id),
            change_number INTEGER NOT NULL,
            revision_digest TEXT NOT NULL,
            eligible BOOLEAN NOT NULL,
            reason_codes TEXT[] NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (profile_id, change_number, revision_digest)
        )""")

    def record_eligibility(self, profile_id: uuid.UUID, change_number: int,
                           review: dict, result: dict) -> None:
        """Count each checked PR revision once; retain no owner, repo or commit."""
        if not 1 <= change_number <= 10_000_000:
            raise ValueError("Invalid change number")
        codes = result.get("reason_codes")
        if (not isinstance(codes, list) or len(codes) > 9
                or any(not isinstance(code, str) or len(code) > 40 for code in codes)):
            raise ValueError("Invalid eligibility reasons")
        revision = f"{review.get('base_sha') or 'missing'}:{review.get('head_sha') or 'missing'}"
        digest = hashlib.sha256(revision.encode()).hexdigest()
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as conn:
            ProjectProfileStore._ensure(conn)
            self._ensure_eligibility(conn)
            conn.execute("""INSERT INTO project_eligibility_checks
                (profile_id, change_number, revision_digest, eligible, reason_codes)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (profile_id, change_number, revision_digest)
                DO UPDATE SET eligible=EXCLUDED.eligible,
                    reason_codes=EXCLUDED.reason_codes, checked_at=now()""",
                (profile_id, change_number, digest, result["eligible"], codes),
            )

    def read(self) -> dict:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as conn:
            ProjectProfileStore._ensure(conn)
            ProjectRunQueue._ensure(conn)
            self._ensure_eligibility(conn)
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
            eligibility = conn.execute("""SELECT
                count(*) AS revisions_checked,
                count(*) FILTER (WHERE eligible) AS eligible_revisions,
                count(*) FILTER (WHERE NOT eligible) AS unsupported_revisions
                FROM project_eligibility_checks""").fetchone()
            unsupported = conn.execute("""SELECT codes.reason_code, count(*) AS revisions
                FROM project_eligibility_checks AS checks
                CROSS JOIN LATERAL unnest(checks.reason_codes) AS codes(reason_code)
                GROUP BY codes.reason_code ORDER BY revisions DESC, codes.reason_code""").fetchall()
            return {
                "profiles": dict(profiles),
                "investigations": dict(runs),
                "failure_codes": [dict(row) for row in failures],
                "eligibility": dict(eligibility),
                "unsupported_reasons": [dict(row) for row in unsupported],
                "scope": "All approved-project pilot records; no owner or repository identities",
                "limitations": [
                    "Cost excludes model, source-host and VM fixed costs; uncertain runs are counted separately.",
                    "An observed change is not a verified mechanism or repair.",
                    "Eligibility counts include checked profile/change revisions only, not every source-host change.",
                ],
            }
