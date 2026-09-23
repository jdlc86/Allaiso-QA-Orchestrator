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

# Bootstrap-stage allowlist. Keep this deliberately narrow until authenticated
# AUT execution is explicitly activated.
PROJECT_POLICIES = {
    "demo": {
        "environment": "test",
        "allowed_url_prefixes": ("https://example.com/",),
        "browser_profile": "qa-demo-public",
        "browser_cdp_port": 18890,
        "browser_color": "#5B8DEF",
    },
    "gestionpisos": {
        "environment": "test",
        "allowed_url_prefixes": ("https://jdlc86.github.io/gestionpisos/",),
        "browser_profile": "qa-gestionpisos-public",
        "browser_cdp_port": 18891,
        "browser_color": "#8B5CF6",
    },
}


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

    project_id = str(job["project_id"])
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted for bootstrap execution: {project_id}")
    if job["environment"] != policy["environment"]:
        raise BlockedFailure(
            f"Environment {job['environment']!r} is not allowed for project {project_id!r}."
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


def openclaw_command(
    binary: str,
    args: list[str],
    platform_name: Optional[str] = None,
) -> list[str]:
    """Build a reliable OpenClaw invocation for the current platform.

    On Windows, Python's direct execution of the npm .CMD shim can remain
    attached even after OpenClaw has produced its output. Prefer the sibling
    PowerShell shim, which is the same path used successfully by the runner's
    native PowerShell steps.
    """
    if (platform_name or os.name) != "nt":
        return [binary, *args]

    path = Path(binary)
    suffix = path.suffix.lower()

    if suffix in {".cmd", ".bat"}:
        powershell_shim = path.with_suffix(".ps1")
        if powershell_shim.is_file():
            path = powershell_shim
            suffix = ".ps1"

    if suffix == ".ps1":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise BlockedFailure(
                "PowerShell is required to execute the OpenClaw .ps1 shim on Windows."
            )
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(path),
            *args,
        ]

    if suffix in {".cmd", ".bat"}:
        command_prompt = shutil.which("cmd.exe") or os.getenv("COMSPEC")
        if not command_prompt:
            raise BlockedFailure(
                "cmd.exe is required to execute the OpenClaw batch shim on Windows."
            )
        return [command_prompt, "/d", "/s", "/c", str(path), *args]

    return [binary, *args]


def safe_label(run_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-")[:48] or "run"
    return f"qa-{value}"


def extract_url(action: str) -> str:
    match = re.search(r"https?://[^\s]+", action)
    return match.group(0).rstrip(".,);]") if match else "https://example.com"


def project_browser_profile(project_id: str) -> str:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    profile = str(policy.get("browser_profile") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", profile):
        raise BlockedFailure(
            f"Project {project_id!r} has an invalid browser profile policy."
        )
    return profile


def project_browser_port(project_id: str) -> int:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    port = policy.get("browser_cdp_port")
    if not isinstance(port, int) or not 18800 <= port <= 18899:
        raise BlockedFailure(
            f"Project {project_id!r} has an invalid managed-browser CDP port policy."
        )
    return port


def project_browser_color(project_id: str) -> str:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    color = str(policy.get("browser_color") or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise BlockedFailure(
            f"Project {project_id!r} has an invalid managed-browser color policy."
        )
    return color


def validate_target_url(project_id: str, url: str) -> None:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise BlockedFailure("Bootstrap navigation requires HTTPS.")

    for prefix in policy["allowed_url_prefixes"]:
        if url == prefix.rstrip("/") or url.startswith(prefix):
            return

    raise BlockedFailure(
        f"Target URL is outside the allowlist for project {project_id!r}: {url}"
    )


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


def meaningful_semantic_name(value: Any) -> bool:
    return isinstance(value, str) and any(char.isalnum() for char in value)


def iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_objects(item)


def snapshot_semantics(stdout: str, parsed: Any) -> tuple[str, str]:
    title = ""
    heading = ""

    # ARIA JSON can expose structured nodes. Prefer those over reparsing the
    # serialized JSON text, which can turn escaped quotes into false matches.
    for item in iter_objects(parsed):
        role = str(item.get("role") or item.get("type") or "").strip().lower()
        name = item.get("name")
        if role == "rootwebarea" and not title and meaningful_semantic_name(name):
            title = str(name).strip()
        if role == "heading" and not heading and meaningful_semantic_name(name):
            heading = str(name).strip()
        if title and heading:
            return title, heading

    # Some OpenClaw versions wrap the ARIA tree as one or more text fields.
    # Inspect decoded JSON strings first; only fall back to raw stdout when no
    # JSON payload was decoded. Never accept punctuation-only captures.
    strings = list(iter_strings(parsed)) if parsed is not None else []
    if not strings and stdout:
        strings = [stdout]

    for text in strings:
        if not title:
            match = re.search(r'(?im)\bRootWebArea\b[^\n"]*"([^"]+)"', text)
            if match and meaningful_semantic_name(match.group(1)):
                title = match.group(1).strip()
        if not heading:
            match = re.search(r'(?im)\bheading\b[^\n"]*"([^"]+)"', text)
            if match and meaningful_semantic_name(match.group(1)):
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
    def __init__(
        self,
        deadline: float,
        profile: str = "openclaw",
        profile_port: Optional[int] = None,
        profile_color: Optional[str] = None,
    ) -> None:
        self.binary = find_openclaw()
        self.deadline = deadline
        self.profile = profile
        self.profile_port = profile_port
        self.profile_color = profile_color

    def run(self, args: list[str], timeout_ms: int = 30000, json_output: bool = False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InfrastructureFailure("Job timeout expired.")
        request_ms = max(1000, min(timeout_ms, int(remaining * 1000)))
        cli_args = [
            "browser", "--browser-profile", self.profile,
            "--timeout", str(request_ms),
        ]
        if json_output:
            cli_args.append("--json")
        cli_args.extend(args)
        command = openclaw_command(self.binary, cli_args)
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

    def run_cli(
        self,
        args: list[str],
        timeout_ms: int = 30000,
        json_output: bool = False,
    ):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InfrastructureFailure("Job timeout expired.")
        request_ms = max(1000, min(timeout_ms, int(remaining * 1000)))
        command = openclaw_command(self.binary, args)
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
                f"OpenClaw CLI command timed out: {' '.join(args)}"
            ) from exc
        stdout = redact((completed.stdout or "").strip())
        stderr = redact((completed.stderr or "").strip())
        return completed.returncode, stdout, stderr, fuzzy_json(stdout) if json_output else None

    def run_unscoped(
        self,
        args: list[str],
        timeout_ms: int = 30000,
        json_output: bool = False,
    ):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InfrastructureFailure("Job timeout expired.")
        request_ms = max(1000, min(timeout_ms, int(remaining * 1000)))
        cli_args = [
            "browser", "--timeout", str(request_ms),
        ]
        if json_output:
            cli_args.append("--json")
        cli_args.extend(args)
        command = openclaw_command(self.binary, cli_args)
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

    def ensure_profile(self) -> None:
        # Persistent browser-profile mutations are rejected when browser
        # requests are routed through a node proxy. Provision the named local
        # managed profile through OpenClaw's config surface instead, then use
        # browser.request only for lifecycle and page operations.
        status, _ = self.status(timeout_ms=5000)
        if status is not None:
            return

        if self.profile_port is None:
            raise InfrastructureFailure(
                f"No CDP port is reserved for isolated profile {self.profile!r}."
            )

        config_path = f"browser.profiles.{self.profile}.cdpPort"
        rc, stdout, stderr, parsed = self.run_cli(
            ["config", "get", config_path, "--json"],
            15000,
            True,
        )
        if rc == 0:
            configured_port = parsed if isinstance(parsed, int) else None
            if configured_port != self.profile_port:
                raise InfrastructureFailure(
                    f"OpenClaw profile {self.profile!r} already exists with "
                    f"unexpected cdpPort={configured_port!r}; expected "
                    f"{self.profile_port}."
                )
            self.wait_for_status()
            return

        detail = stderr or stdout or "no diagnostic output"
        missing = "Config path not found" in detail or (
            isinstance(parsed, dict)
            and "Config path not found" in str(parsed.get("error", ""))
        )
        if not missing:
            raise InfrastructureFailure(
                f"OpenClaw config lookup failed for profile {self.profile!r} "
                f"(rc={rc}): {detail}"
            )

        if not self.profile_color:
            raise InfrastructureFailure(
                f"No color is reserved for isolated profile {self.profile!r}."
            )

        profile_path = f"browser.profiles.{self.profile}"
        profile_value = json.dumps(
            {"cdpPort": self.profile_port, "color": self.profile_color},
            separators=(",", ":"),
        )
        rc, stdout, stderr, _ = self.run_cli(
            [
                "config",
                "set",
                profile_path,
                profile_value,
                "--strict-json",
            ],
            30000,
            False,
        )
        if rc != 0:
            detail = stderr or stdout or "no diagnostic output"
            raise InfrastructureFailure(
                f"OpenClaw could not provision isolated profile "
                f"{self.profile!r} in local config (rc={rc}): {detail}"
            )

        self.wait_for_status()

    def status(self, timeout_ms: int = 15000) -> tuple[Optional[dict[str, Any]], str]:
        try:
            rc, stdout, stderr, parsed = self.run(["status"], timeout_ms, True)
        except InfrastructureFailure as exc:
            return None, str(exc)
        if rc != 0:
            return None, stderr or stdout or "browser status returned no diagnostic output"
        if not isinstance(parsed, dict):
            return None, "browser status did not return a JSON object"
        return parsed, ""

    def wait_for_status(
        self,
        attempts: int = 8,
        delay_seconds: float = 2.0,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        diagnostic = "browser status unavailable"
        for attempt in range(attempts):
            status, diagnostic = self.status(timeout_ms=timeout_ms)
            if status is not None:
                return status
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)

        raise InfrastructureFailure(
            f"OpenClaw profile {self.profile!r} did not become addressable "
            f"after Gateway reload: {diagnostic}"
        )

    @staticmethod
    def status_ready(status: Optional[dict[str, Any]]) -> bool:
        return bool(
            isinstance(status, dict)
            and status.get("running") is True
            and status.get("cdpReady") is True
        )

    def start(self) -> None:
        self.ensure_profile()

        # A previous run can leave the dedicated managed browser alive. An
        # unconditional second start has exhausted the request timeout on the
        # Windows QA runner, so probe passive readiness first.
        status, _ = self.status()
        if self.status_ready(status):
            return

        self.require(["start"], 60000)

        status, diagnostic = self.status()
        if not self.status_ready(status):
            if status is not None:
                diagnostic = (
                    f"running={status.get('running')!r}, "
                    f"cdpReady={status.get('cdpReady')!r}"
                )
            raise InfrastructureFailure(
                f"OpenClaw browser did not become ready after start: {diagnostic}"
            )

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
        browser = Browser(
            deadline,
            profile=project_browser_profile(project_id),
            profile_port=project_browser_port(project_id),
            profile_color=project_browser_color(project_id),
        )
        browser.start()
        label = safe_label(run_id)

        for raw in job["steps"]:
            step_id = str(raw.get("id") or "unnamed")
            action = str(raw.get("action") or "").strip()
            expect = str(raw.get("expect") or "").strip()

            try:
                if action.startswith("Navigate to"):
                    url = extract_url(action)
                    validate_target_url(project_id, url)
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
            except BlockedFailure as exc:
                steps_out.append(step(step_id, "BLOCKED", str(exc)))
                diagnostics.append(f"Step {step_id} blocked: {redact(str(exc))}")
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
