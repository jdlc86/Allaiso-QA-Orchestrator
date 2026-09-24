#!/usr/bin/env python3
"""Minimal dependency-free validator for bootstrap jobs.
Full JSON Schema validation can be added once runtime dependencies are pinned.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ALLOWED_ENV = {"local", "development", "staging", "test", "production"}
ALLOWED_MODE = {"scenario", "steps"}


def fail(msg: str) -> None:
    print(f"INVALID: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_job(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # optional; bootstrap may not have PyYAML
    except ImportError:
        fail("YAML job requires PyYAML. Use JSON for dependency-free bootstrap or install pinned dependencies when provided.")
    return yaml.safe_load(text)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate_job.py <job.json|job.yaml>")
    p = Path(sys.argv[1])
    if not p.is_file(): fail(f"job not found: {p}")
    j = load_job(p)
    if not isinstance(j, dict): fail("job root must be an object")
    required = ["protocol_version","job_id","run_id","project_id","requested_by","objective","environment","mode","assertions","evidence","safety"]
    for k in required:
        if k not in j: fail(f"missing required field: {k}")
    if j["protocol_version"] != "0.1": fail("unsupported protocol_version")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(j["project_id"])): fail("invalid project_id")
    if j["environment"] not in ALLOWED_ENV: fail("invalid environment")
    if j["mode"] not in ALLOWED_MODE: fail("invalid mode")
    if j["mode"] == "scenario" and not j.get("scenario"): fail("scenario mode requires scenario")
    if j["mode"] == "steps" and not j.get("steps"): fail("steps mode requires steps")
    if not isinstance(j["assertions"], list) or not j["assertions"]: fail("assertions must be non-empty")
    safety = j["safety"]
    if not isinstance(safety, dict) or not all(k in safety for k in ("destructive_actions","production_writes")): fail("invalid safety block")
    controlled = j.get("controlled_write")
    if controlled is not None:
        if not isinstance(controlled, dict): fail("controlled_write must be an object")
        required_controlled = ("write_scope","action_family","fixture_key","request_key")
        if not all(k in controlled for k in required_controlled): fail("invalid controlled_write block")
        if controlled["write_scope"] != "fixtures_only": fail("controlled_write.write_scope must be fixtures_only")
        if not isinstance(controlled["request_key"], str) or not 8 <= len(controlled["request_key"]) <= 160: fail("invalid controlled_write.request_key")
    print(json.dumps({"valid": True, "job_id": j["job_id"], "run_id": j["run_id"], "project_id": j["project_id"]}))

if __name__ == "__main__":
    main()
