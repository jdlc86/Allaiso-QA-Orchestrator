#!/usr/bin/env python3
import sys
import unittest
from unittest.mock import Mock, patch

from executor import (
    Browser,
    InfrastructureFailure,
    openclaw_command,
    project_browser_color,
    project_browser_port,
    project_browser_profile,
    run_process_captured,
    snapshot_semantics,
)


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


class OpenClawInvocationTests(unittest.TestCase):
    def test_windows_cmd_prefers_powershell_shim(self):
        with patch("executor.Path.is_file", return_value=True), patch(
            "executor.shutil.which",
            side_effect=lambda name: (
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
                if name in {"powershell.exe", "powershell"}
                else None
            ),
        ):
            command = openclaw_command(
                r"C:\Users\julop\AppData\Roaming\npm\openclaw.CMD",
                ["browser", "--json", "status"],
                platform_name="nt",
            )

        self.assertEqual(
            command,
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                r"C:\Users\julop\AppData\Roaming\npm\openclaw.ps1",
                "browser",
                "--json",
                "status",
            ],
        )

    def test_non_windows_invokes_binary_directly(self):
        command = openclaw_command(
            "/usr/local/bin/openclaw",
            ["browser", "status"],
            platform_name="posix",
        )
        self.assertEqual(
            command,
            ["/usr/local/bin/openclaw", "browser", "status"],
        )


class ProcessCaptureTests(unittest.TestCase):
    def test_tempfile_capture_preserves_stdout_and_stderr(self):
        rc, stdout, stderr = run_process_captured(
            [
                sys.executable,
                "-c",
                "import sys; print('stdout-ok'); print('stderr-ok', file=sys.stderr)",
            ],
            5,
        )
        self.assertEqual(rc, 0)
        self.assertIn("stdout-ok", stdout)
        self.assertIn("stderr-ok", stderr)


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
        self.assertEqual(project_browser_port("demo"), 18890)
        self.assertEqual(project_browser_color("demo"), "#5B8DEF")
        self.assertEqual(
            project_browser_profile("gestionpisos"),
            "qa-gestionpisos-public",
        )
        self.assertEqual(project_browser_port("gestionpisos"), 18891)
        self.assertEqual(project_browser_color("gestionpisos"), "#8B5CF6")

    def test_existing_profile_is_reused_without_config_write(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.profile_port = 18891
        browser.profile_color = "#8B5CF6"
        browser.status = Mock(return_value=(
            {"running": False, "cdpReady": False},
            "",
        ))
        browser.run_cli = Mock()

        browser.ensure_profile()

        browser.status.assert_called_once_with(timeout_ms=5000)
        browser.run_cli.assert_not_called()

    def test_configured_profile_port_is_reused(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.profile_port = 18891
        browser.profile_color = "#8B5CF6"
        browser.status = Mock(return_value=(None, "gateway reloading"))
        browser.run_cli = Mock(return_value=(0, "18891", "", 18891))
        browser.wait_for_status = Mock(return_value={
            "running": False,
            "cdpReady": False,
        })

        browser.ensure_profile()

        browser.run_cli.assert_called_once_with(
            [
                "config",
                "get",
                "browser.profiles.qa-gestionpisos-public.cdpPort",
                "--json",
            ],
            15000,
            True,
        )
        browser.wait_for_status.assert_called_once_with()

    def test_missing_profile_is_provisioned_through_config(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.profile_port = 18891
        browser.profile_color = "#8B5CF6"
        browser.status = Mock(side_effect=[
            (None, "unknown browser profile"),
            ({"running": False, "cdpReady": False}, ""),
        ])
        browser.run_cli = Mock(side_effect=[
            (
                1,
                '{"ok":false,"error":{"message":"Config path not found: browser.profiles.qa-gestionpisos-public.cdpPort"}}',
                "",
                {"ok": False, "error": {"message": "Config path not found: browser.profiles.qa-gestionpisos-public.cdpPort"}},
            ),
            (0, "Updated", "", None),
        ])
        browser.wait_for_status = Mock(return_value={
            "running": False,
            "cdpReady": False,
        })

        browser.ensure_profile()

        self.assertEqual(browser.run_cli.call_count, 2)
        self.assertEqual(
            browser.run_cli.call_args_list[1].args[0],
            [
                "config",
                "set",
                "browser.profiles.qa-gestionpisos-public",
                '{"cdpPort":18891,"color":"#8B5CF6"}',
                "--strict-json",
            ],
        )
        browser.wait_for_status.assert_called_once_with()

    def test_wait_for_status_retries_gateway_reload(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(side_effect=[
            (None, "gateway closed"),
            (None, "gateway restarting"),
            ({"running": False, "cdpReady": False}, ""),
        ])

        with patch("executor.time.sleep") as sleep:
            status = browser.wait_for_status(
                attempts=3,
                delay_seconds=0.1,
                timeout_ms=5000,
            )

        self.assertEqual(status["running"], False)
        self.assertEqual(browser.status.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_wait_for_status_reports_last_gateway_error(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.status = Mock(return_value=(None, "gateway still unavailable"))

        with patch("executor.time.sleep"):
            with self.assertRaisesRegex(
                InfrastructureFailure,
                "gateway still unavailable",
            ):
                browser.wait_for_status(
                    attempts=2,
                    delay_seconds=0.1,
                    timeout_ms=5000,
                )

    def test_existing_profile_with_unexpected_port_is_rejected(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.profile_port = 18891
        browser.profile_color = "#8B5CF6"
        browser.status = Mock(return_value=(None, "unknown browser profile"))
        browser.run_cli = Mock(return_value=(0, "18842", "", 18842))

        with self.assertRaises(InfrastructureFailure):
            browser.ensure_profile()

    def test_config_lookup_other_failure_is_reported(self):
        browser = object.__new__(Browser)
        browser.profile = "qa-gestionpisos-public"
        browser.profile_port = 18891
        browser.profile_color = "#8B5CF6"
        browser.status = Mock(return_value=(None, "unknown browser profile"))
        browser.run_cli = Mock(return_value=(1, "", "permission denied", None))

        with self.assertRaises(InfrastructureFailure):
            browser.ensure_profile()


if __name__ == "__main__":
    unittest.main()
