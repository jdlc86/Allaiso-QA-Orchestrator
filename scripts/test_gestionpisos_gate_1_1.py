#!/usr/bin/env python3
import json
import unittest
from unittest import mock

from executor import BlockedFailure
from gestionpisos_gate_1_1 import (
    EXPECTED_ACTION,
    GATE,
    _gate_payload,
    evaluate_fixed,
    build_apply_script,
    build_prepare_script,
    build_verify_script,
    validate_gate_job,
)


FIXTURE = {
    "property_id": "3518ddd8-5062-4047-ab1e-0f098ca1e150",
    "property_name": "REFPISO1",
    "property_required_status": "active",
    "room_id": "e0e94ab3-4e97-49e6-8280-27cc7e55248b",
    "room_label": "REFPISO1_HAB1",
    "room_required_status": "active",
    "occupancy_id": "149623e6-0b7e-431d-b9b1-282951e43262",
    "occupancy_required_status": "active",
    "occupancy_requires_linked_user": True,
}
ACTION_CONFIG = {
    "definition_request_key": "qa-gate-1-1-checklist-definition-v1",
    "flow_name": "QA Gate 1.1 Checklist",
    "checklist_items": [
        {"text": "Verificar fixture QA", "required": True},
        {"text": "Confirmar cierre idempotente", "required": True},
    ],
}


class GatePayloadTests(unittest.TestCase):
    def test_reads_direct_payload(self):
        payload = {"gate": GATE, "phase": "verify", "ok": True}
        self.assertEqual(_gate_payload(payload), payload)

    def test_reads_nested_openclaw_result(self):
        payload = {"gate": GATE, "phase": "verify", "ok": True}
        wrapped = {"targetId": "tab", "result": json.dumps(payload)}
        self.assertEqual(_gate_payload(wrapped), payload)

    def test_rejects_unrelated_json(self):
        self.assertIsNone(_gate_payload({"ok": True, "result": 2}))


class EvaluateInvocationTests(unittest.TestCase):
    def test_uses_single_line_async_function_and_global_timeout(self):
        browser = mock.Mock()
        browser.require.side_effect = [
            ("", None),
            ('{"gate":"workflow_checklist_gate_1_1","phase":"test","ok":true}', {"gate": "workflow_checklist_gate_1_1", "phase": "test", "ok": True}),
        ]
        payload = evaluate_fixed(
            browser,
            "tab-label",
            """
            const value = await Promise.resolve(1);
            return {gate: "workflow_checklist_gate_1_1", phase: "test", ok: value === 1};
            """,
            30000,
        )
        self.assertTrue(payload["ok"])
        evaluate_call = browser.require.call_args_list[1]
        args = evaluate_call.args[0]
        self.assertEqual(args[:2], ["evaluate", "--fn"])
        self.assertNotIn("--timeout-ms", args)
        self.assertTrue(args[2].startswith("async () => { "))
        self.assertTrue(args[2].endswith(" }"))
        self.assertNotIn("\n", args[2])
        self.assertIn("await Promise.resolve(1)", args[2])

class FixedJavascriptTests(unittest.TestCase):
    def test_prepare_script_is_fixed_to_official_rpc_and_fixture(self):
        script = build_prepare_script(FIXTURE, ACTION_CONFIG, "qa-gate11-test-0001")
        self.assertIn('publish_workflow_ready_v1', script)
        self.assertIn(FIXTURE["property_id"], script)
        self.assertIn(FIXTURE["room_id"], script)
        self.assertIn(FIXTURE["occupancy_id"], script)
        self.assertIn(ACTION_CONFIG["definition_request_key"], script)
        self.assertIn(ACTION_CONFIG["flow_name"], script)
        self.assertNotIn("service_role", script.lower())
        self.assertNotIn("access_token", script.lower())
        self.assertNotIn("refresh_token", script.lower())
        self.assertNotIn("password", script.lower())

    def test_apply_script_only_mutates_checklist_rpc(self):
        script = build_apply_script("task-fixture", "qa-gate11-test-0001")
        self.assertEqual(script.count('set_workflow_checklist_item_v1'), 3)
        self.assertIn('"item-1"', script)
        self.assertIn('"item-2"', script)
        self.assertNotIn("publish_workflow_ready_v1", script)

    def test_verify_script_is_read_only(self):
        script = build_verify_script(
            "execution-fixture",
            "task-fixture",
            "qa-gate11-test-0001",
        )
        self.assertIn("workflow_execution_events_v2", script)
        self.assertIn("tenant_task_history_v2", script)
        self.assertNotIn(".rpc(", script)


class GateJobTests(unittest.TestCase):
    def base_job(self):
        return {
            "project_id": "gestionpisos",
            "session_mode": "authenticated_reuse",
            "steps": [{"id": "gate", "action": EXPECTED_ACTION}],
            "controlled_write": {
                "action_family": GATE,
            },
        }

    def test_accepts_only_exact_fixed_action(self):
        validate_gate_job(self.base_job())

    def test_rejects_extra_action(self):
        job = self.base_job()
        job["steps"].append({"id": "other", "action": "Click anything"})
        with self.assertRaisesRegex(BlockedFailure, "exactly one fixed action"):
            validate_gate_job(job)

    def test_rejects_other_action_family(self):
        job = self.base_job()
        job["controlled_write"]["action_family"] = "arbitrary"
        with self.assertRaisesRegex(BlockedFailure, "action family mismatch"):
            validate_gate_job(job)


if __name__ == "__main__":
    unittest.main()
