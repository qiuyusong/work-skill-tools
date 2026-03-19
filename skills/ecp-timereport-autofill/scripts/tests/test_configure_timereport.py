import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import device_binding

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "configure_timereport.py"


class ConfigureTimereportTests(unittest.TestCase):
    def test_show_required_status_reports_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "timereport-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "device_binding": {"fingerprint": device_binding.current_device_fingerprint()},
                        "projects": [],
                        "ecp": {"username": "", "password": ""},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--config", str(config_path), "--show-required-status"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ready"])
            self.assertEqual(payload["missing_fields"], ["projects", "ecp.username", "ecp.password"])
            self.assertEqual(payload["project_count"], 0)
            self.assertIsNone(payload["binding_issue"])
            self.assertFalse(payload["requires_reconfigure"])

    def test_show_required_status_reports_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "timereport-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "device_binding": {"fingerprint": "mismatch-demo"},
                        "projects": [{"name": "repo-a", "path": "E:\\repo-a"}],
                        "ecp": {"username": "demo-user", "password": "demo-pass"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--config", str(config_path), "--show-required-status"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ready"])
            self.assertEqual(payload["missing_fields"], [])
            self.assertEqual(payload["binding_issue"], "fingerprint_mismatch")
            self.assertTrue(payload["requires_reconfigure"])


if __name__ == "__main__":
    unittest.main()
