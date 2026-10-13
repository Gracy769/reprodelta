"""Credential-free smoke check for a disposable, mock-model deployment.

Never target a live billable deployment: this harness expects mock proposals.
Persist its cookie only in an ignored temporary state file for restart checks.
"""

import argparse
import json
import time
from pathlib import Path

import httpx

PROJECT = "00000000-0000-0000-0000-000000000001"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=["create", "restart", "interrupt", "recovery"], required=True
    )
    args = parser.parse_args()
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    with httpx.Client(base_url=args.url, timeout=30, cookies=state.get("cookies", {})) as client:
        for _ in range(60):
            try:
                if client.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("API did not start")
        ready = client.get("/ready")
        ready.raise_for_status()
        assert ready.json()["checks"]["worker_old"] == "ok"
        assert ready.json()["checks"]["worker_new"] == "ok"
        if args.phase in ("create", "interrupt"):
            approval = client.post(f"/api/projects/{PROJECT}/contract/approve")
            approval.raise_for_status()
            response = client.post(
                "/api/investigations", json={"project_id": PROJECT, "max_runs": 400}
            )
            response.raise_for_status()
            inv = response.json()["id"]
            started = client.post(f"/api/investigations/{inv}/start")
            started.raise_for_status()
            state["cookies"] = dict(client.cookies)
            if args.phase == "interrupt":
                state["interrupted_id"] = inv
                assert client.get(f"/api/investigations/{inv}").json()["status"] == "running"
            else:
                for _ in range(300):
                    progress = client.get(f"/api/investigations/{inv}/progress").json()
                    if progress["status"] in ("completed", "failed"):
                        break
                    time.sleep(1)
                assert progress["status"] == "completed", progress
                report_response = client.get(f"/api/investigations/{inv}/report")
                report_response.raise_for_status()
                report = report_response.json()
                assert report["run_mode"] == "pinned_workers"
                assert any(item["is_mock"] for item in report["model_proposals"])
                assert report["model_patch_result"]["verdict"] == "repair_passes_stated_checks"
                assert report["demonstration_comparison"]["status"] == "completed"
                state.update(id=inv, report=report)
        elif args.phase == "restart":
            assert client.get(f"/api/investigations/{state['id']}/report").json() == state["report"]
            assert client.get(f"/api/investigations/{state['id']}/evidence").status_code == 200
            assert client.get(f"/api/investigations/{state['id']}/report/text").status_code == 200
        else:
            inv = state["interrupted_id"]
            assert client.get(f"/api/investigations/{inv}").json()["status"] == "interrupted"
            assert client.get(f"/api/investigations/{inv}/report").status_code == 404
            assert client.get(f"/api/investigations/{state['id']}/report").json() == state["report"]
        with httpx.Client(base_url=args.url) as stranger:
            assert (
                stranger.get(
                    f"/api/investigations/{state.get('id', state.get('interrupted_id'))}"
                ).status_code
                == 404
            )
    args.state.write_text(json.dumps(state), encoding="utf-8")
    print("Passed:", args.phase)


if __name__ == "__main__":
    main()
