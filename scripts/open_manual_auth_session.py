#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

from executor import (
    Browser,
    InfrastructureFailure,
    classify_gestionpisos_auth_snapshot,
    project_browser_color,
    project_browser_port,
    project_browser_profile,
    repo_root,
    snapshot_semantics,
)

PROJECT_ID = "gestionpisos"
SESSION_MODE = "authenticated_reuse"
TARGET_URL = "https://jdlc86.github.io/gestionpisos/"


def write_result(payload: dict) -> Path:
    output_dir = repo_root() / ".runtime"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "manual-auth-bootstrap-result.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    label = f"manual-auth-{run_id}"
    browser = Browser(
        time.monotonic() + 300,
        profile=project_browser_profile(PROJECT_ID, SESSION_MODE),
        profile_port=project_browser_port(PROJECT_ID, SESSION_MODE),
        profile_color=project_browser_color(PROJECT_ID, SESSION_MODE),
    )

    try:
        browser.start()
        browser.open(TARGET_URL, label)
    except InfrastructureFailure as exc:
        path = write_result({
            "status": "ERROR",
            "profile": browser.profile,
            "url": TARGET_URL,
            "surface": "unknown",
            "diagnostic": str(exc),
        })
        print(f"Manual auth bootstrap failed. Result: {path}")
        return 1

    # Best-effort semantic classification only. The tab intentionally remains
    # open after this script exits so a human can enter credentials/MFA
    # directly in Chrome without exposing them to GitHub, logs, or ChatGPT.
    surface = "unknown"
    title = ""
    heading = ""
    diagnostic = ""
    try:
        time.sleep(2)
        stdout, parsed = browser.snapshot(label)
        title, heading = snapshot_semantics(stdout, parsed)
        surface = classify_gestionpisos_auth_snapshot(title, heading)
    except InfrastructureFailure as exc:
        diagnostic = str(exc)

    path = write_result({
        "status": "OPENED_FOR_HUMAN_AUTH",
        "profile": browser.profile,
        "url": TARGET_URL,
        "surface": surface,
        "title": title,
        "heading": heading,
        "tab_label": label,
        "diagnostic": diagnostic,
        "instructions": (
            "Complete login and MFA directly in the visible Chrome window if required. "
            "Do not share credentials or TOTP codes with the orchestrator."
        ),
    })

    print(
        "Manual authentication tab is open and intentionally left running; "
        f"surface={surface!r}; result={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
