#!/usr/bin/env python3
"""Controlled GestionPisos Gate 1.1 Checklist runner.

This runner deliberately supports exactly one fixed write scenario. It does not
accept arbitrary browser JavaScript, selectors, RPC names, or navigation from
the job. Authorization is supplied by projects/gestionpisos/write-policy.json.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

from executor import (
    BlockedFailure,
    Browser,
    InfrastructureFailure,
    authorize_controlled_write,
    classify_gestionpisos_auth_snapshot,
    job_session_mode,
    load_job,
    load_project_write_policy,
    media_path,
    project_browser_color,
    project_browser_port,
    project_browser_profile,
    redact,
    repo_root,
    safe_label,
    snapshot_semantics,
    utc_now,
    validate_job,
)

GATE = "workflow_checklist_gate_1_1"
EXPECTED_ACTION = "Run GestionPisos Gate 1.1 controlled checklist"
BASE_URL = "https://jdlc86.github.io/gestionpisos/"
HISTORY_URL = BASE_URL + "workflow-history.html"
SUPABASE_MODULE_URL = BASE_URL + "supabase-client.js"


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _gate_payload(value: Any, depth: int = 0) -> Optional[dict[str, Any]]:
    if depth > 6:
        return None
    if isinstance(value, dict):
        if value.get("gate") == GATE and isinstance(value.get("ok"), bool):
            return value
        for key in ("result", "value", "data", "output"):
            if key in value:
                found = _gate_payload(value[key], depth + 1)
                if found is not None:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _gate_payload(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _gate_payload(decoded, depth + 1)
    return None


def evaluate_fixed(
    browser: Browser,
    label: str,
    body: str,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    browser.require(["focus", label], 30000)
    # Browser.require already applies OpenClaw's global --timeout option.
    # OpenClaw 2026.5.12 expects --fn to receive one function expression.
    # Keep it single-line so the Windows PowerShell npm shim preserves it as
    # one argv item instead of fragmenting multiline JavaScript.
    compact_body = " ".join(
        line.strip() for line in body.splitlines() if line.strip()
    )
    browser_fn = f"async () => {{ {compact_body} }}"
    stdout, parsed = browser.require(
        ["evaluate", "--fn", browser_fn],
        timeout_ms + 5000,
        True,
    )
    payload = _gate_payload(parsed)
    if payload is None:
        raise InfrastructureFailure(
            "OpenClaw evaluate returned no structured Gate 1.1 payload: "
            + redact(stdout[:1000])
        )
    return payload


def verify_authenticated_surface(browser: Browser, label: str) -> None:
    last_title = ""
    last_heading = ""
    for attempt in range(8):
        stdout, parsed = browser.snapshot(label)
        title, heading = snapshot_semantics(stdout, parsed)
        last_title, last_heading = title, heading
        state = classify_gestionpisos_auth_snapshot(title, heading)
        if state == "authenticated":
            return
        if state == "login":
            raise BlockedFailure(
                "The isolated QA profile has no reusable authenticated session or it expired."
            )
        if state == "mfa_challenge":
            raise BlockedFailure("The QA session requires an MFA challenge.")
        if state == "mfa_setup":
            raise BlockedFailure("The QA session requires MFA enrollment.")
        if attempt < 7:
            time.sleep(1)
    raise InfrastructureFailure(
        "Could not classify authenticated GestionPisos surface; "
        f"title={last_title!r}, heading={last_heading!r}."
    )


def build_prepare_script(
    fixture: dict[str, Any],
    action_config: dict[str, Any],
    request_key: str,
) -> str:
    spec = {
        "authoringVersion": 2,
        "flowName": action_config["flow_name"],
        "flowType": "custom",
        "flowDescription": "Fixture QA automatizado para Gate 1.1 Checklist.",
        "scopeType": "property",
        "triggerType": "manual",
        "recurrence": "",
        "scheduledAt": "",
        "customEvery": "",
        "customUnit": "",
        "assignmentType": "manual",
        "steps": {
            "accept": False,
            "photo": False,
            "checklist": True,
            "document": False,
        },
        "checklistItems": action_config["checklist_items"],
        "closeType": "auto",
        "notifications": {"onCreate": False, "onClose": False},
    }
    cfg = {
        "gate": GATE,
        "moduleUrl": SUPABASE_MODULE_URL,
        "propertyId": fixture["property_id"],
        "propertyName": fixture["property_name"],
        "propertyStatus": fixture["property_required_status"],
        "roomId": fixture["room_id"],
        "roomLabel": fixture["room_label"],
        "roomStatus": fixture["room_required_status"],
        "occupancyId": fixture["occupancy_id"],
        "occupancyStatus": fixture["occupancy_required_status"],
        "requiresLinkedUser": fixture["occupancy_requires_linked_user"],
        "definitionRequestKey": action_config["definition_request_key"],
        "requestKey": request_key,
        "spec": spec,
    }
    return f"""
const cfg = {_js(cfg)};
const fail = (phase, error) => ({{gate: cfg.gate, phase, ok: false, error: String(error?.message || error || "unknown")}});
try {{
  const mod = await import(cfg.moduleUrl);
  const supabase = mod.supabase;
  if (!supabase) return fail("import", "supabase_client_missing");

  const auth = await supabase.auth.getUser();
  if (auth.error || !auth.data?.user?.id) return fail("auth", auth.error || "authenticated_user_missing");
  const userId = auth.data.user.id;

  const propertyQ = await supabase.from("properties_v2")
    .select("id,name,status,archived_at")
    .eq("id", cfg.propertyId).maybeSingle();
  if (propertyQ.error || !propertyQ.data
      || propertyQ.data.name !== cfg.propertyName
      || propertyQ.data.status !== cfg.propertyStatus
      || propertyQ.data.archived_at !== null) {{
    return fail("precondition", propertyQ.error || "property_fixture_mismatch");
  }}

  const roomQ = await supabase.from("rooms_v2")
    .select("id,property_id,label,status,archived_at")
    .eq("id", cfg.roomId).maybeSingle();
  if (roomQ.error || !roomQ.data
      || roomQ.data.property_id !== cfg.propertyId
      || roomQ.data.label !== cfg.roomLabel
      || roomQ.data.status !== cfg.roomStatus
      || roomQ.data.archived_at !== null) {{
    return fail("precondition", roomQ.error || "room_fixture_mismatch");
  }}

  const occupancyQ = await supabase.from("occupancies_v2")
    .select("id,property_id,room_id,status,user_id")
    .eq("id", cfg.occupancyId).maybeSingle();
  if (occupancyQ.error || !occupancyQ.data
      || occupancyQ.data.property_id !== cfg.propertyId
      || occupancyQ.data.room_id !== cfg.roomId
      || occupancyQ.data.status !== cfg.occupancyStatus
      || (cfg.requiresLinkedUser && !occupancyQ.data.user_id)) {{
    return fail("precondition", occupancyQ.error || "occupancy_fixture_mismatch");
  }}

  const publishQ = await supabase.rpc("publish_workflow_ready_v1", {{
    p_spec: cfg.spec,
    p_property_id: cfg.propertyId,
    p_room_id: null,
    p_occupancy_id: null,
    p_photo_pattern_ids: [],
    p_execute: true,
    p_request_key: cfg.definitionRequestKey,
    p_idempotency_key: cfg.requestKey + ":execute",
    p_assigned_user_id: userId
  }});
  if (publishQ.error) return fail("publish_execute", publishQ.error);
  const row = Array.isArray(publishQ.data) ? publishQ.data[0] : publishQ.data;
  if (!row?.definition_id || !row?.application_id || !row?.execution_id) {{
    return fail("publish_execute", "publish_execute_receipt_incomplete");
  }}

  const definitionCountQ = await supabase.from("workflow_definitions_v2")
    .select("id", {{count: "exact", head: true}})
    .eq("creation_request_key", cfg.definitionRequestKey);
  if (definitionCountQ.error) return fail("verify_definition", definitionCountQ.error);

  const executionCountQ = await supabase.from("workflow_executions_v2")
    .select("id", {{count: "exact", head: true}})
    .eq("application_id", row.application_id)
    .eq("idempotency_key", cfg.requestKey + ":execute");
  if (executionCountQ.error) return fail("verify_execution", executionCountQ.error);

  let taskRows = [];
  for (let attempt = 0; attempt < 8; attempt++) {{
    const taskQ = await supabase.from("tenant_tasks_v2")
      .select("id,status,assigned_user_id,source_id,source_kind")
      .eq("source_kind", "workflow_execution")
      .eq("source_id", row.execution_id);
    if (taskQ.error) return fail("verify_task", taskQ.error);
    taskRows = Array.isArray(taskQ.data) ? taskQ.data : [];
    if (taskRows.length === 1) break;
    await new Promise(resolve => setTimeout(resolve, 250));
  }}
  if (taskRows.length !== 1) return fail("verify_task", "expected_exactly_one_task");
  if (taskRows[0].assigned_user_id !== userId) return fail("verify_task", "task_not_assigned_to_current_qa_user");

  if (definitionCountQ.count !== 1) return fail("verify_definition", "expected_exactly_one_definition");
  if (executionCountQ.count !== 1) return fail("verify_execution", "expected_exactly_one_execution");

  return {{
    gate: cfg.gate,
    phase: "prepare",
    ok: true,
    definition_id: row.definition_id,
    version_id: row.version_id,
    application_id: row.application_id,
    execution_id: row.execution_id,
    task_id: taskRows[0].id,
    definition_count: definitionCountQ.count,
    execution_count: executionCountQ.count,
    task_count: taskRows.length
  }};
}} catch (error) {{
  return fail("unexpected", error);
}}
"""


def build_apply_script(task_id: str, request_key: str) -> str:
    cfg = {
        "gate": GATE,
        "moduleUrl": SUPABASE_MODULE_URL,
        "taskId": task_id,
        "requestKey": request_key,
    }
    return f"""
const cfg = {_js(cfg)};
const fail = (phase, error) => ({{gate: cfg.gate, phase, ok: false, error: String(error?.message || error || "unknown")}});
try {{
  const mod = await import(cfg.moduleUrl);
  const supabase = mod.supabase;
  const firstQ = await supabase.rpc("set_workflow_checklist_item_v1", {{
    p_task_id: cfg.taskId,
    p_item_key: "item-1",
    p_completed: true,
    p_request_key: cfg.requestKey + ":item-1"
  }});
  if (firstQ.error) return fail("item_1", firstQ.error);

  const secondQ = await supabase.rpc("set_workflow_checklist_item_v1", {{
    p_task_id: cfg.taskId,
    p_item_key: "item-2",
    p_completed: true,
    p_request_key: cfg.requestKey + ":item-2"
  }});
  if (secondQ.error) return fail("item_2", secondQ.error);

  const replayQ = await supabase.rpc("set_workflow_checklist_item_v1", {{
    p_task_id: cfg.taskId,
    p_item_key: "item-2",
    p_completed: true,
    p_request_key: cfg.requestKey + ":item-2"
  }});
  if (replayQ.error) return fail("item_2_replay", replayQ.error);

  return {{
    gate: cfg.gate,
    phase: "apply",
    ok: true,
    item_1_rows: Array.isArray(firstQ.data) ? firstQ.data.length : 0,
    item_2_rows: Array.isArray(secondQ.data) ? secondQ.data.length : 0,
    replay_rows: Array.isArray(replayQ.data) ? replayQ.data.length : 0
  }};
}} catch (error) {{
  return fail("unexpected", error);
}}
"""


def build_verify_script(execution_id: str, task_id: str, request_key: str) -> str:
    cfg = {
        "gate": GATE,
        "moduleUrl": SUPABASE_MODULE_URL,
        "executionId": execution_id,
        "taskId": task_id,
        "requestKey": request_key,
    }
    return f"""
const cfg = {_js(cfg)};
const fail = (phase, error) => ({{gate: cfg.gate, phase, ok: false, error: String(error?.message || error || "unknown")}});
try {{
  const mod = await import(cfg.moduleUrl);
  const supabase = mod.supabase;

  const executionQ = await supabase.from("workflow_executions_v2")
    .select("id,status,checklist_state")
    .eq("id", cfg.executionId).maybeSingle();
  if (executionQ.error || !executionQ.data) return fail("verify_final_execution", executionQ.error || "execution_missing");

  const taskQ = await supabase.from("tenant_tasks_v2")
    .select("id,status,source_id")
    .eq("id", cfg.taskId).maybeSingle();
  if (taskQ.error || !taskQ.data) return fail("verify_final_task", taskQ.error || "task_missing");

  const eventsQ = await supabase.from("workflow_execution_events_v2")
    .select("id,from_status,to_status,details")
    .eq("execution_id", cfg.executionId)
    .eq("event_type", "checklist_item_changed")
    .order("id", {{ascending: true}});
  if (eventsQ.error) return fail("verify_events", eventsQ.error);
  const events = Array.isArray(eventsQ.data) ? eventsQ.data : [];
  const item1Key = cfg.requestKey + ":item-1";
  const item2Key = cfg.requestKey + ":item-2";
  const item1Event = events.find(e => e?.details?.request_key === item1Key);
  const item2Event = events.find(e => e?.details?.request_key === item2Key);

  const historyQ = await supabase.from("tenant_task_history_v2")
    .select("id", {{count: "exact", head: true}})
    .eq("task_id", cfg.taskId)
    .eq("action_key", "checklist_item_completed");
  if (historyQ.error) return fail("verify_history", historyQ.error);

  const state = Array.isArray(executionQ.data.checklist_state) ? executionQ.data.checklist_state : [];
  const required = state.filter(item => item.required !== false);
  const allRequiredComplete = required.length === 2 && required.every(item => item.completed === true);

  const item1StayedOpen = Boolean(
    item1Event
    && item1Event.details?.all_required_complete === false
    && item1Event.to_status !== "completed"
  );
  const item2Closed = Boolean(
    item2Event
    && item2Event.details?.all_required_complete === true
    && item2Event.to_status === "completed"
  );

  const ok = executionQ.data.status === "completed"
    && taskQ.data.status === "completed"
    && taskQ.data.source_id === cfg.executionId
    && allRequiredComplete
    && events.length === 2
    && item1StayedOpen
    && item2Closed
    && historyQ.count === 2;

  return {{
    gate: cfg.gate,
    phase: "verify",
    ok,
    execution_status: executionQ.data.status,
    task_status: taskQ.data.status,
    required_items: required.length,
    completed_required_items: required.filter(item => item.completed === true).length,
    checklist_event_count: events.length,
    checklist_history_count: historyQ.count,
    item_1_stayed_open: item1StayedOpen,
    item_2_closed: item2Closed,
    error: ok ? null : "gate_1_1_invariant_failed"
  }};
}} catch (error) {{
  return fail("unexpected", error);
}}
"""


def validate_gate_job(job: dict[str, Any]) -> None:
    if job.get("project_id") != "gestionpisos":
        raise BlockedFailure("Gate 1.1 runner is GestionPisos-only.")
    if job_session_mode(job) != "authenticated_reuse":
        raise BlockedFailure("Gate 1.1 requires authenticated_reuse.")
    actions = [
        str(step.get("action") or "").strip()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]
    if actions != [EXPECTED_ACTION]:
        raise BlockedFailure("Gate 1.1 runner accepts exactly one fixed action.")
    controlled = job.get("controlled_write") or {}
    if controlled.get("action_family") != GATE:
        raise BlockedFailure("Gate 1.1 action family mismatch.")


def classify_payload_failure(payload: dict[str, Any]) -> Exception:
    phase = str(payload.get("phase") or "unknown")
    error = str(payload.get("error") or "unknown")
    combined = f"{phase}: {error}"
    if phase in {"auth", "precondition"} or any(
        marker in error.lower()
        for marker in (
            "aal2_required",
            "not_authenticated",
            "session",
            "workflow_manual_assignee_not_eligible",
        )
    ):
        return BlockedFailure(combined)
    return RuntimeError(combined)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: gestionpisos_gate_1_1.py <job.json>", file=sys.stderr)
        return 2

    started_at = utc_now()
    job_id = "unknown"
    run_id = f"gate11-{int(time.time())}"
    status = "ERROR"
    diagnostics: list[str] = []
    evidence: list[str] = []
    steps: list[dict[str, str]] = []
    assertions: list[dict[str, Any]] = []
    gate_receipt: dict[str, Any] = {}
    browser: Optional[Browser] = None
    label: Optional[str] = None

    try:
        job = load_job(Path(sys.argv[1]).expanduser().resolve())
        job_id = str(job.get("job_id") or job_id)
        run_id = str(job.get("run_id") or run_id)
        validate_job(job)
        validate_gate_job(job)

        fixture = authorize_controlled_write(job)
        policy = load_project_write_policy("gestionpisos")
        action_configs = policy.get("action_configs") or {}
        action_config = action_configs.get(GATE) if isinstance(action_configs, dict) else None
        if not isinstance(action_config, dict):
            raise BlockedFailure("Gate 1.1 fixed action configuration is missing.")

        request_key = str((job.get("controlled_write") or {}).get("request_key") or "")
        deadline = time.monotonic() + int(job.get("timeout_seconds", 600))
        browser = Browser(
            deadline,
            profile=project_browser_profile("gestionpisos", "authenticated_reuse"),
            profile_port=project_browser_port("gestionpisos", "authenticated_reuse"),
            profile_color=project_browser_color("gestionpisos", "authenticated_reuse"),
        )
        browser.start()
        label = safe_label(run_id)
        browser.open(BASE_URL, label)
        verify_authenticated_surface(browser, label)
        steps.append({"id": "auth", "status": "PASSED", "observation": "Reusable authenticated session is ready."})

        prepare = evaluate_fixed(
            browser,
            label,
            build_prepare_script(fixture, action_config, request_key),
        )
        if not prepare.get("ok"):
            raise classify_payload_failure(prepare)
        gate_receipt.update({
            key: prepare.get(key)
            for key in (
                "definition_id", "version_id", "application_id",
                "execution_id", "task_id", "definition_count",
                "execution_count", "task_count",
            )
        })
        steps.append({
            "id": "prepare",
            "status": "PASSED",
            "observation": "Fixture preconditions passed; definition/application/execution/task are uniquely materialized.",
        })

        apply_result = evaluate_fixed(
            browser,
            label,
            build_apply_script(str(prepare["task_id"]), request_key),
        )
        if not apply_result.get("ok"):
            raise classify_payload_failure(apply_result)
        steps.append({
            "id": "apply",
            "status": "PASSED",
            "observation": "Checklist item 1, item 2, and same-key replay completed through the official RPC.",
        })

        verify = evaluate_fixed(
            browser,
            label,
            build_verify_script(
                str(prepare["execution_id"]),
                str(prepare["task_id"]),
                request_key,
            ),
        )
        if not verify.get("ok"):
            raise RuntimeError(
                f"verify: {verify.get('error') or 'gate_1_1_invariant_failed'}"
            )
        gate_receipt.update({
            key: verify.get(key)
            for key in (
                "execution_status", "task_status", "required_items",
                "completed_required_items", "checklist_event_count",
                "checklist_history_count", "item_1_stayed_open",
                "item_2_closed",
            )
        })
        steps.append({
            "id": "verify",
            "status": "PASSED",
            "observation": "Gate 1.1 invariants passed, including premature-close prevention and idempotent replay.",
        })

        browser.require(["focus", label], 30000)
        browser.require(["navigate", HISTORY_URL], 30000)
        history_visible = False
        history_text = ""
        for attempt in range(10):
            stdout, parsed = browser.snapshot(label)
            history_text = stdout + "\n" + json.dumps(parsed, ensure_ascii=False)
            if action_config["flow_name"] in history_text:
                history_visible = True
                break
            if attempt < 9:
                time.sleep(1)
        if not history_visible:
            raise RuntimeError("History surface did not show the Gate 1.1 fixture flow.")
        steps.append({
            "id": "history_ui",
            "status": "PASSED",
            "observation": "The completed Gate 1.1 fixture is visible in GestionPisos Historial.",
        })

        screenshot_stdout = browser.screenshot(label)
        source = media_path(screenshot_stdout)
        if source is None:
            raise InfrastructureFailure("History screenshot did not return a MEDIA path.")
        evidence_dir = repo_root() / "evidence" / "gestionpisos" / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        destination = evidence_dir / f"gate-1-1-history{source.suffix.lower() or '.png'}"
        shutil.copy2(source, destination)
        evidence.append(destination.relative_to(repo_root()).as_posix())
        steps.append({
            "id": "evidence",
            "status": "PASSED",
            "observation": "History screenshot captured after controlled write verification.",
        })

        assertions = [
            {"assertion": "Authenticated GestionPisos session is ready.", "passed": True, "observation": "READY"},
            {"assertion": "Gate 1.1 checklist invariants passed.", "passed": True, "observation": "PASSED"},
            {"assertion": "Gate 1.1 history is visible.", "passed": True, "observation": "VISIBLE"},
            {"assertion": "A screenshot was captured.", "passed": True, "observation": "CAPTURED"},
        ]
        status = "PASSED"

    except BlockedFailure as exc:
        status = "BLOCKED"
        diagnostics.append(redact(str(exc)))
    except InfrastructureFailure as exc:
        status = "ERROR"
        diagnostics.append(redact(str(exc)))
    except Exception as exc:
        status = "FAILED"
        diagnostics.append(redact(str(exc)))
    finally:
        if browser is not None and label:
            browser.close(label)

    result = {
        "protocol_version": "0.1",
        "job_id": job_id,
        "run_id": run_id,
        "project_id": "gestionpisos",
        "executor": "openclaw-controlled-gate",
        "gate": "1.1-checklist",
        "started_at": started_at,
        "finished_at": utc_now(),
        "status": status,
        "steps": steps,
        "assertions": assertions,
        "diagnostics": diagnostics,
        "evidence": evidence,
        "receipt": gate_receipt,
        "summary": (
            f"Gate 1.1 finished with {status}; {len(steps)} step result(s), "
            f"{len(assertions)} assertion(s), {len(evidence)} evidence file(s)."
        ),
    }
    output_dir = repo_root() / "results" / "gestionpisos" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Result written to {result_path}")
    print(f"Overall status: {status}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
