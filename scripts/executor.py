#!/usr/bin/env python3
"""
Bootstrap executor for Allaiso-QA-Orchestrator using OpenClaw browser.

Protocol 0.1. This first canonical executor intentionally supports only the
harmless smoke action family used to prove the QA node. Real AUT actions are
added deliberately after the node handshake is verified.

Verified OpenClaw 2026.5.12 Windows path:
start -> open(label) -> snapshot(label) -> focus(label) -> screenshot
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

PROTOCOL_VERSION = "0.1"
STEP_STATUSES = {"PASSED", "FAILED", "BLOCKED", "ERROR", "SKIPPED"}
RESULT_STATUSES = {"PASSED", "FAILED", "BLOCKED", "ERROR", "CANCELLED"}


class BlockedFailure(Exception):
    pass


class InfrastructureFailure(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(value: str) -> str:
    if not value:
        return ""
    patterns = [
        (r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
        (r"(?i)(--token(?:=|\s+))[^\s]+", r"\1[REDACTED]"),
        (r"\bghp_[A-Za-z0-9]{20,}\b", "[REDACTED_GITHUB_TOKEN]"),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED_GITHUB_TOKEN]"),
        (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[REDACTED_JWT]"),
    ]
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_job(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BlockedFailure(f"Job not found: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    elif path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise BlockedFailure("YAML jobs require PyYAML; use JSON for bootstrap.") from exc
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raise BlockedFailure(f"Unsupported job format: {path.suffix}")
    if not isinstance(data, dict):
        raise BlockedFailure("Job root must be an object.")
    return data


def validate_job(job: dict[str, Any]) -> None:
    required = [
        "protocol_version", "job_id", "run_id", "project_id", "requested_by",
        "objective", "environment", "mode", "assertions", "evidence", "safety",
    ]
    missing = [key for key in required if key not in job]
    if missing:
        raise BlockedFailure(f"Missing required fields: {', '.join(missing)}")
    if job["protocol_version"] != PROTOCOL_VERSION:
        raise BlockedFailure(f"Unsupported protocol_version: {job['protocol_version']!r}")
    if job["mode"] != "steps" or not isinstance(job.get("steps"), list) or not job["steps"]:
        raise BlockedFailure("Bootstrap executor requires non-empty steps mode.")
    timeout = job.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or timeout < 1 or timeout > 7200:
        raise BlockedFailure("timeout_seconds must be between 1 and 7200.")
    safety = job.get("safety")
    if not isinstance(safety, dict):
        raise BlockedFailure("safety must be an object.")
    if safety.get("destructive_actions") or safety.get("production_writes"):
        raise BlockedFailure("Bootstrap executor only permits non-destructive/no-write jobs.")
    if job["project_id"] != "demo":
        raise BlockedFailure(
            "Bootstrap executor is intentionally restricted to project_id='demo'."
        )


def find_openclaw() -> str:
    override = os.getenv("OPENCLAW_BIN")
    if override:
        resolved = shutil.which(override)
        if resolved:
            return resolved
        candidate = Path(os.path.expandvars(os.path.expanduser(override)))
        if candidate.exists():
            return str(candidate)
        raise BlockedFailure(f"OPENCLAW_BIN is unavailable: {override}")
    resolved = shutil.which("openclaw")
    if not resolved:
        raise BlockedFailure(
            "OpenClaw CLI is not on PATH for this execution identity. "
            "Set OPENCLAW_BIN if necessary."
        )
    return resolved


def safe_label(run_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-")[:48] or "run"
    return f"qa-{value}"


def extract_url(action: str) -> str:
    match = re.search(r"https?://[^\s]+", action)
    return match.group(0).rstrip(".,);]") if match else "https://example.com"


def iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def fuzzy_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def snapshot_semantics(stdout: str, parsed: Any) -> tuple[str, str]:
    strings = list(iter_strings(parsed))
    if stdout:
        strings.append(stdout)
    title = ""
    heading = ""
    for text in strings:
        if not title:
            match = re.search(r'(?im)\bRootWebArea\b[^\n"]*"([^"]+)"', text)
            if match:
                title = match.group(1).strip()
        if not heading:
            match = re.search(r'(?im)\bheading\b[^\n"]*"([^"]+)"', text)
            if match:
                heading = match.group(1).strip()
        if title and heading:
            break
    return title, heading


def normalize_media_path(raw: str) -> str:
    raw = raw.strip().strip('"').strip("'")
    if raw.upper().startswith("MEDIA:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
    return raw


def media_path(stdout: str) -> Optional[Path]:
    match = re.search(r"MEDIA:\s*(.+?\.(?:png|jpe?g|webp))(?:\s|$)", stdout, re.I)
    if not match:
        return None
    raw = normalize_media_path(match.group(1))
    raw = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(raw)
    attempts = [candidate]
    if not candidate.is_absolute():
        attempts += [Path.home() / candidate, repo_root() / candidate]
    for path in attempts:
        if path.exists() and path.is_file():
            return path.resolve()
    return None


class Browser:
    def __init__(self, deadline: float, profile: str = "openclaw") -> None:
        self.binary = find_openclaw()
        self.deadline = deadline
        self.profile = profile

    def run(self, args: list[str], timeout_ms: int = 30000, json_output: bool = False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InfrastructureFailure("Job timeout expired.")
        request_ms = max(1000, min(timeout_ms, int(remaining * 1000)))
        command = [
            self.binary, "browser", "--browser-profile", self.profile,
            "--timeout", str(request_ms),
        ]
        if json_output:
            command.append("--json")
        command.extend(args)
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root(),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, min(remaining, request_ms / 1000 + 12)),
            )
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureFailure(
                f"OpenClaw command timed out: {' '.join(args)}"
            ) from exc
        stdout = redact((completed.stdout or "").strip())
        stderr = redact((completed.stderr or "").strip())
        return completed.returncode, stdout, stderr, fuzzy_json(stdout) if json_output else None

    def require(self, args: list[str], timeout_ms: int = 30000, json_output: bool = False):
        rc, stdout, stderr, parsed = self.run(args, timeout_ms, json_output)
        if rc != 0:
            detail = stderr or stdout or "no diagnostic output"
            raise InfrastructureFailure(
                f"OpenClaw {' '.join(args)} failed (rc={rc}): {detail}"
            )
        return stdout, parsed

    def start(self) -> None:
        self.require(["start"], 30000)

    def open(self, url: str, label: str) -> None:
        self.require(["open", url, "--label", label], 30000)

    def snapshot(self, label: str) -> tuple[str, Any]:
        return self.require(
            ["snapshot", "--target-id", label, "--format", "aria", "--limit", "200"],
            30000,
            True,
        )

    def screenshot(self, label: str) -> str:
        self.require(["focus", label], 30000)
        stdout, _ = self.require(["screenshot", "--full-page"], 30000)
        return stdout

    def close(self, label: str) -> None:
        try:
            self.run(["close", label], 10000)
        except Exception:
            pass


def step(step_id: str, status: str, observation: str) -> dict[str, str]:
    if status not in STEP_STATUSES:
        raise ValueError(status)
    return {"id": step_id, "status": status, "observation": redact(observation)}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: executor.py <job.json|job.yaml>", file=sys.stderr)
        return 2

    started_at = utc_now()
    job_id = "unknown"
    run_id = f"run-{int(time.time())}"
    project_id = "unknown"
    steps_out: list[dict[str, str]] = []
    assertions_out: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    evidence: list[str] = []
    error: Optional[str] = None
    browser: Optional[Browser] = None
    label: Optional[str] = None
    navigation_ok = semantic_ok = screenshot_ok = False

    try:
        job = load_job(Path(sys.argv[1]).expanduser().resolve())
        job_id = str(job.get("job_id") or job_id)
        run_id = str(job.get("run_id") or run_id)
        project_id = str(job.get("project_id") or project_id)
        validate_job(job)

        deadline = time.monotonic() + int(job.get("timeout_seconds", 900))
        browser = Browser(deadline)
        browser.start()
        label = safe_label(run_id)

        for raw in job["steps"]:
            step_id = str(raw.get("id") or "unnamed")
            action = str(raw.get("action") or "").strip()
            expect = str(raw.get("expect") or "").strip()

            try:
                if action.startswith("Navigate to"):
                    url = extract_url(action)
                    browser.open(url, label)
                    navigation_ok = True
                    steps_out.append(step(
                        step_id, "PASSED",
                        f"Opened harmless target {url}. Expected: {expect or 'navigation succeeds'}."
                    ))
                elif action.startswith("Read the page title"):
                    stdout, parsed = browser.snapshot(label)
                    title, heading = snapshot_semantics(stdout, parsed)
                    semantic_ok = bool(title and heading)
                    if not semantic_ok:
                        steps_out.append(step(
                            step_id, "FAILED",
                            f"Semantic snapshot missing title/heading; title={title!r}, heading={heading!r}."
                        ))
                        break
                    steps_out.append(step(
                        step_id, "PASSED",
                        f"Title={title!r}; first visible heading={heading!r}. "
                        f"Expected: {expect or 'semantic content can be read'}."
                    ))
                elif action.startswith("Capture a screenshot"):
                    stdout = browser.screenshot(label)
                    source = media_path(stdout)
                    if source is None:
                        raise InfrastructureFailure(
                            "Screenshot command succeeded but MEDIA file could not be resolved."
                        )
                    evidence_dir = repo_root() / "evidence" / project_id / run_id
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    destination = evidence_dir / f"screenshot{source.suffix.lower() or '.png'}"
                    shutil.copy2(source, destination)
                    relative = destination.relative_to(repo_root()).as_posix()
                    evidence.append(relative)
                    screenshot_ok = True
                    steps_out.append(step(
                        step_id, "PASSED",
                        f"Screenshot stored as {relative}. "
                        f"Expected: {expect or 'screenshot evidence exists'}."
                    ))
                else:
                    steps_out.append(step(
                        step_id, "BLOCKED", f"Unsupported bootstrap action: {action}"
                    ))
                    diagnostics.append(f"Unsupported action: {action}")
                    break
            except InfrastructureFailure as exc:
                steps_out.append(step(step_id, "ERROR", str(exc)))
                diagnostics.append(f"Step {step_id} error: {redact(str(exc))}")
                break

        for assertion in job["assertions"]:
            assertion = str(assertion)
            if assertion == "Navigation completed successfully.":
                passed, observation = navigation_ok, f"Navigation: {'OK' if navigation_ok else 'FAIL'}"
            elif assertion == "At least one visible semantic element was read.":
                passed, observation = semantic_ok, f"Semantic inspection: {'OK' if semantic_ok else 'FAIL'}"
            elif assertion == "A screenshot was captured.":
                passed, observation = screenshot_ok, f"Screenshot evidence: {'OK' if screenshot_ok else 'FAIL'}"
            else:
                passed, observation = False, "Unsupported assertion."
                diagnostics.append(f"Unsupported assertion: {assertion}")
            assertions_out.append({
                "assertion": assertion,
                "passed": bool(passed),
                "observation": observation,
            })

        statuses = {item["status"] for item in steps_out}
        if "ERROR" in statuses:
            status = "ERROR"
        elif "BLOCKED" in statuses:
            status = "BLOCKED"
        elif "FAILED" in statuses or any(not item["passed"] for item in assertions_out):
            status = "FAILED"
        else:
            status = "PASSED"

    except BlockedFailure as exc:
        status = "BLOCKED"
        error = redact(str(exc))
        diagnostics.append(error)
    except InfrastructureFailure as exc:
        status = "ERROR"
        error = redact(str(exc))
        diagnostics.append(error)
    except Exception as exc:
        status = "ERROR"
        error = f"Unexpected executor error: {redact(str(exc))}"
        diagnostics.append(error)
    finally:
        if browser is not None and label:
            browser.close(label)

    result = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": job_id,
        "run_id": run_id,
        "project_id": project_id,
        "executor": "openclaw",
        "started_at": started_at,
        "finished_at": utc_now(),
        "status": status,
        "steps": steps_out,
        "assertions": assertions_out,
        "diagnostics": diagnostics,
        "evidence": evidence,
        "error": error,
        "summary": (
            f"Executor finished with {status}; {len(steps_out)} step result(s), "
            f"{len(assertions_out)} assertion(s), {len(evidence)} evidence file(s)."
        ),
    }

    if result["status"] not in RESULT_STATUSES:
        result["status"] = "ERROR"
        result["error"] = "Executor produced an invalid status."

    output_dir = repo_root() / "results" / project_id / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / "result.json"
    result_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Result written to {result_file}")
    print(f"Overall status: {result['status']}")
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
