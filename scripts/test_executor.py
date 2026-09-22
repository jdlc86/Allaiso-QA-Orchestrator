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

    def test_existing_profile_is_reused_without_global_listing(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(return_value=(
            {"running": False, "cdpReady": False},
            "",
        ))
        browser.run_unscoped = Mock()

        browser.ensure_profile()

        browser.status.assert_called_once_with(timeout_ms=5000)
        browser.run_unscoped.assert_not_called()

    def test_missing_profile_is_created_without_global_listing(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(return_value=(None, "unknown browser profile"))
        browser.run_unscoped = Mock(return_value=(0, "created", "", None))

        browser.ensure_profile()

        browser.status.assert_called_once_with(timeout_ms=5000)
        browser.run_unscoped.assert_called_once_with(
            ["create-profile", "--name", "qa-gestionpisos-public"],
            30000,
            False,
        )

    def test_create_profile_already_exists_is_idempotent(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(return_value=(None, "status timeout"))
        browser.run_unscoped = Mock(return_value=(
            1,
            "",
            "Browser profile already exists: qa-gestionpisos-public",
            None,
        ))

        browser.ensure_profile()

        browser.run_unscoped.assert_called_once()

    def test_create_profile_other_failure_is_reported(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(return_value=(None, "unknown browser profile"))
        browser.run_unscoped = Mock(return_value=(
            1,
            "",
            "permission denied",
            None,
        ))

        with self.assertRaises(InfrastructureFailure):
            browser.ensure_profile()


if __name__ == "__main__":
    unittest.main()
