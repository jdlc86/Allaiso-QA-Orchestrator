#!/usr/bin/env python3
import unittest
from unittest.mock import Mock

from executor import Browser, InfrastructureFailure, project_browser_profile, snapshot_semantics


class SnapshotSemanticsTests(unittest.TestCase):
    def test_reads_structured_aria_nodes(self):
        parsed = {
            "nodes": [
                {"role": "RootWebArea", "name": "Example Domain"},
                {"role": "heading", "name": "Example Domain"},
            ]
        }
        self.assertEqual(
            snapshot_semantics("", parsed),
            ("Example Domain", "Example Domain"),
        )

    def test_reads_decoded_text_wrapper(self):
        parsed = {
            "snapshot": 'RootWebArea "Example Domain"\nheading "Example Domain"'
        }
        self.assertEqual(
            snapshot_semantics("", parsed),
            ("Example Domain", "Example Domain"),
        )

    def test_rejects_punctuation_only_raw_json_false_positive(self):
        stdout = (
            '{"nodes":[{"role":"RootWebArea","name":","},'
            '{"role":"heading","name":","}]}'
        )
        parsed = {
            "nodes": [
                {"role": "RootWebArea", "name": ","},
                {"role": "heading", "name": ","},
            ]
        }
        self.assertEqual(snapshot_semantics(stdout, parsed), ("", ""))


class BrowserReadinessTests(unittest.TestCase):
    def test_ready_requires_running_and_cdp(self):
        self.assertTrue(Browser.status_ready({"running": True, "cdpReady": True}))
        self.assertFalse(Browser.status_ready({"running": True, "cdpReady": False}))
        self.assertFalse(Browser.status_ready({"running": False, "cdpReady": True}))
        self.assertFalse(Browser.status_ready(None))

    def test_status_timeout_becomes_diagnostic_not_abort(self):
        browser = object.__new__(Browser)
        browser.run = Mock(side_effect=InfrastructureFailure("status timeout"))
        status, diagnostic = browser.status()
        self.assertIsNone(status)
        self.assertEqual(diagnostic, "status timeout")

    def test_profile_policy_is_project_scoped(self):
        self.assertEqual(project_browser_profile("demo"), "qa-demo-public")
        self.assertEqual(
            project_browser_profile("gestionpisos"),
            "qa-gestionpisos-public",
        )

    def test_existing_profile_is_reused(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.run_unscoped = Mock(return_value=(
            0,
            '{"profiles":[{"name":"qa-gestionpisos-public"}]}',
            "",
            {"profiles": [{"name": "qa-gestionpisos-public"}]},
        ))
        browser.ensure_profile()
        self.assertEqual(browser.run_unscoped.call_count, 1)

    def test_missing_profile_is_created_once(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.run_unscoped = Mock(side_effect=[
            (0, '{"profiles":[]}', "", {"profiles": []}),
            (0, "created", "", None),
        ])
        browser.ensure_profile()
        self.assertEqual(browser.run_unscoped.call_count, 2)
        self.assertEqual(
            browser.run_unscoped.call_args_list[1].args[0],
            ["create-profile", "--name", "qa-gestionpisos-public"],
        )


if __name__ == "__main__":
    unittest.main()
