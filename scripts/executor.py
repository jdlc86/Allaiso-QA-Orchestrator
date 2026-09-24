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
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

PROTOCOL_VERSION = "0.1"
STEP_STATUSES = {"PASSED", "FAILED", "BLOCKED", "ERROR", "SKIPPED"}
RESULT_STATUSES = {"PASSED", "FAILED", "BLOCKED", "ERROR", "CANCELLED"}
SESSION_MODES = {"public", "authenticated_reuse"}

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
        "authenticated_browser_profile": "qa-gestionpisos-auth",
        "authenticated_browser_cdp_port": 18892,
        "authenticated_browser_color": "#D97706",
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


def load_project_write_policy(project_id: str) -> dict[str, Any]:
    path = repo_root() / "projects" / project_id / "write-policy.json"
    if not path.is_file():
        raise BlockedFailure(
            f"Controlled write policy is not configured for project {project_id!r}."
        )
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockedFailure(
            f"Controlled write policy for project {project_id!r} is unreadable."
        ) from exc
    if not isinstance(policy, dict):
        raise BlockedFailure("Controlled write policy root must be an object.")
    if policy.get("protocol_version") != PROTOCOL_VERSION:
        raise BlockedFailure("Controlled write policy protocol_version mismatch.")
    if policy.get("project_id") != project_id:
        raise BlockedFailure("Controlled write policy project_id mismatch.")
    return policy


def authorize_controlled_write(job: dict[str, Any]) -> dict[str, Any]:
    request = job.get("controlled_write")
    if request is None:
        return {}
    if not isinstance(request, dict):
        raise BlockedFailure("controlled_write must be an object.")

    project_id = str(job.get("project_id") or "")
    policy = load_project_write_policy(project_id)
    if policy.get("enabled") is not True:
        raise BlockedFailure(
            f"Controlled writes are disabled for project {project_id!r}."
        )
    if job.get("environment") != policy.get("environment"):
        raise BlockedFailure("Controlled write environment is not allowlisted.")
    if job_session_mode(job) != "authenticated_reuse":
        raise BlockedFailure("Controlled writes require authenticated_reuse session mode.")
    if request.get("write_scope") != policy.get("write_scope"):
        raise BlockedFailure("Controlled write scope is not allowlisted.")

    action_family = str(request.get("action_family") or "")
    allowed_families = policy.get("allowed_action_families") or []
    if action_family not in allowed_families:
        raise BlockedFailure(
            f"Controlled write action family is not allowlisted: {action_family!r}."
        )

    fixture_key = str(request.get("fixture_key") or "")
    fixtures = policy.get("fixtures") or {}
    fixture = fixtures.get(fixture_key) if isinstance(fixtures, dict) else None
    if not isinstance(fixture, dict):
        raise BlockedFailure(
            f"Controlled write fixture is not allowlisted: {fixture_key!r}."
        )

    request_key = str(request.get("request_key") or "")
    if len(request_key) < 8 or len(request_key) > 160:
        raise BlockedFailure("Controlled write request_key must be 8..160 characters.")

    return fixture


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
    session_mode = job_session_mode(job)
    if session_mode == "authenticated_reuse" and not policy.get("authenticated_browser_profile"):
        raise BlockedFailure(
            f"Authenticated session reuse is not enabled for project {project_id!r}."
        )

    authorize_controlled_write(job)


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


def run_process_captured(command: list[str], timeout_seconds: float):
    """Run a CLI without PIPE-backed stdio.

    OpenClaw can leave descendant processes holding inherited PIPE handles on
    Windows, which makes subprocess.communicate() wait until timeout even after
    the CLI itself has finished. Temporary files avoid that pipe-lifetime
    coupling while preserving stdout/stderr for diagnostics.
    """
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
        completed = subprocess.run(
            command,
            cwd=repo_root(),
            shell=False,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
    return completed.returncode, stdout, stderr


def safe_label(run_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-")[:48] or "run"
    return f"qa-{value}"


def extract_url(action: str) -> str:
    match = re.search(r"https?://[^\s]+", action)
    return match.group(0).rstrip(".,);]") if match else "https://example.com"


def job_session_mode(job: dict[str, Any]) -> str:
    mode = str(job.get("session_mode") or "public").strip()
    if mode not in SESSION_MODES:
        raise BlockedFailure(f"Unsupported session_mode: {mode!r}")
    return mode


def project_browser_profile(project_id: str, session_mode: str = "public") -> str:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    if session_mode not in SESSION_MODES:
        raise BlockedFailure(f"Unsupported session_mode: {session_mode!r}")
    key = "browser_profile" if session_mode == "public" else "authenticated_browser_profile"
    profile = str(policy.get(key) or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", profile):
        raise BlockedFailure(
            f"Project {project_id!r} has an invalid browser profile policy."
        )
    return profile


def project_browser_port(project_id: str, session_mode: str = "public") -> int:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    if session_mode not in SESSION_MODES:
        raise BlockedFailure(f"Unsupported session_mode: {session_mode!r}")
    key = "browser_cdp_port" if session_mode == "public" else "authenticated_browser_cdp_port"
    port = policy.get(key)
    if not isinstance(port, int) or not 18800 <= port <= 18899:
        raise BlockedFailure(
            f"Project {project_id!r} has an invalid managed-browser CDP port policy."
        )
    return port


def project_browser_color(project_id: str, session_mode: str = "public") -> str:
    policy = PROJECT_POLICIES.get(project_id)
    if policy is None:
        raise BlockedFailure(f"Project is not allowlisted: {project_id}")
    if session_mode not in SESSION_MODES:
        raise BlockedFailure(f"Unsupported session_mode: {session_mode!r}")
    key = "browser_color" if session_mode == "public" else "authenticated_browser_color"
    color = str(policy.get(key) or "").strip()
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


def classify_gestionpisos_auth_snapshot(title: str, heading: str) -> str:
    """Classify only known GestionPisos authentication surfaces.

    This deliberately uses fixed product semantics instead of accepting arbitrary
    selectors or JavaScript from a job. It is therefore read-only and keeps the
    authenticated bootstrap allowlist narrow.
    """
    title = title.strip()
    heading = heading.strip()
    if title == "GestionPisos" and heading == "GestionPisos":
        return "authenticated"
    if title == "Allaiso · Acceso" or heading == "Acceso a GestionPisos":
        return "login"
    if title == "Allaiso · Verificación MFA" or heading == "Segundo factor":
        return "mfa_challenge"
    if title == "Allaiso · Seguridad MFA" or heading == "Seguridad MFA":
        return "mfa_setup"
    return "unknown"


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
            returncode, raw_stdout, raw_stderr = run_process_captured(
                command,
                max(1.0, min(remaining, request_ms / 1000 + 12)),
            )
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureFailure(
                f"OpenClaw command timed out: {' '.join(args)}"
            ) from exc
        stdout = redact((raw_stdout or "").strip())
        stderr = redact((raw_stderr or "").strip())
        return returncode, stdout, stderr, fuzzy_json(stdout) if json_output else None

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
            returncode, raw_stdout, raw_stderr = run_process_captured(
                command,
                max(1.0, min(remaining, request_ms / 1000 + 12)),
            )
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureFailure(
                f"OpenClaw CLI command timed out: {' '.join(args)}"
            ) from exc
        stdout = redact((raw_stdout or "").strip())
        stderr = redact((raw_stderr or "").strip())
        return returncode, stdout, stderr, fuzzy_json(stdout) if json_output else None

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
            returncode, raw_stdout, raw_stderr = run_process_captured(
                command,
                max(1.0, min(remaining, request_ms / 1000 + 12)),
            )
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureFailure(
                f"OpenClaw command timed out: {' '.join(args)}"
            ) from exc
        stdout = redact((raw_stdout or "").strip())
        stderr = redact((raw_stderr or "").strip())
        return returncode, stdout, stderr, fuzzy_json(stdout) if json_output else None

    def ensure_gateway(self) -> None:
        # The managed Gateway service is the control plane for browser
        # requests. gateway start is intentionally idempotent: on a healthy
        # running service it is a no-op; on a registered stopped service it
        # starts it. This removes the need for a human to pre-start OpenClaw.
        rc, stdout, stderr, _ = self.run_cli(
            ["gateway", "start", "--json"],
            60000,
            True,
        )
        if rc != 0:
            detail = stderr or stdout or "no diagnostic output"
            raise InfrastructureFailure(
                f"OpenClaw Gateway could not be started (rc={rc}): {detail}"
            )

    def ensure_profile(self) -> None:
        # Persistent browser-profile mutations are rejected when browser
        # requests are routed through a node proxy. Provision the named local
        # managed profile through OpenClaw's config surface instead, then use
        # browser.request only for lifecycle and page operations.
        status, _ = self.status(timeout_ms=15000)
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

        # Do not pass a structured JSON value through the Windows PowerShell
        # npm shim: inner quotes are lost before OpenClaw receives the argument.
        # Also do not create required profile fields one at a time because the
        # config writer validates the whole profile after each write. A
        # config-shaped patch file preserves quoting and applies cdpPort +
        # color atomically.
        patch_payload = {
            "browser": {
                "profiles": {
                    self.profile: {
                        "cdpPort": self.profile_port,
                        "color": self.profile_color,
                    }
                }
            }
        }
        with tempfile.TemporaryDirectory(prefix="allaiso-openclaw-profile-") as temp_dir:
            patch_path = Path(temp_dir) / "profile.patch.json"
            patch_path.write_text(
                json.dumps(patch_payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            rc, stdout, stderr, _ = self.run_cli(
                ["config", "patch", "--file", str(patch_path)],
                30000,
                False,
            )
        if rc != 0:
            detail = stderr or stdout or "no diagnostic output"
            raise InfrastructureFailure(
                f"OpenClaw could not provision isolated profile "
                f"{self.profile!r} atomically in local config (rc={rc}): {detail}"
            )

        # Applying a config patch can restart or stop the Gateway before the
        # new profile becomes addressable. Reassert the idempotent Gateway
        # start here, then wait for the browser profile to appear.
        self.ensure_gateway()
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
        attempts: int = 4,
        delay_seconds: float = 2.0,
        timeout_ms: int = 15000,
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
        self.ensure_gateway()
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
    navigation_ok = semantic_ok = screenshot_ok = authenticated_session_ok = False

    try:
        job = load_job(Path(sys.argv[1]).expanduser().resolve())
        job_id = str(job.get("job_id") or job_id)
        run_id = str(job.get("run_id") or run_id)
        project_id = str(job.get("project_id") or project_id)
        validate_job(job)
        session_mode = job_session_mode(job)

        deadline = time.monotonic() + int(job.get("timeout_seconds", 900))
        browser = Browser(
            deadline,
            profile=project_browser_profile(project_id, session_mode),
            profile_port=project_browser_port(project_id, session_mode),
            profile_color=project_browser_color(project_id, session_mode),
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
                elif action == "Verify GestionPisos authenticated session":
                    if project_id != "gestionpisos" or session_mode != "authenticated_reuse":
                        raise BlockedFailure(
                            "Authenticated-session verification is only allowed for "
                            "GestionPisos authenticated_reuse jobs."
                        )

                    last_title = ""
                    last_heading = ""
                    for attempt in range(8):
                        stdout, parsed = browser.snapshot(label)
                        title, heading = snapshot_semantics(stdout, parsed)
                        last_title, last_heading = title, heading
                        auth_state = classify_gestionpisos_auth_snapshot(title, heading)

                        if auth_state == "authenticated":
                            authenticated_session_ok = True
                            semantic_ok = True
                            steps_out.append(step(
                                step_id,
                                "PASSED",
                                "GestionPisos authenticated surface is visible without "
                                "credential entry or AUT mutation."
                            ))
                            break
                        if auth_state == "login":
                            raise BlockedFailure(
                                "The isolated QA browser profile has no reusable authenticated "
                                "session or the session expired."
                            )
                        if auth_state == "mfa_challenge":
                            raise BlockedFailure(
                                "The reusable QA session requires an MFA challenge before "
                                "authenticated read-only testing can continue."
                            )
                        if auth_state == "mfa_setup":
                            raise BlockedFailure(
                                "The reusable QA session requires MFA enrollment before "
                                "authenticated read-only testing can continue."
                            )
                        if attempt < 7:
                            time.sleep(1)
                    else:
                        steps_out.append(step(
                            step_id,
                            "FAILED",
                            "Could not classify the authenticated GestionPisos surface after "
                            f"waiting; title={last_title!r}, heading={last_heading!r}."
                        ))
                        break
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
            elif assertion == "Authenticated GestionPisos session is ready.":
                passed = authenticated_session_ok
                observation = (
                    "Authenticated session: READY"
                    if authenticated_session_ok
                    else "Authenticated session: NOT READY"
                )
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
