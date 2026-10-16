"""Exercise reset orchestration with a fake Docker executable, never real services."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "reset-demo.sh"


class ResetDemoTests(unittest.TestCase):
    def run_wrapper(self, *arguments, confirmation="RESET ecommerce-backend-prod\n", failure=""):
        """Run an isolated checkout with recorded Docker calls and injected failures."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            shutil.copy2(SCRIPT, root / "scripts/reset-demo.sh")
            binary = root / "docker"
            binary.write_text(r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_TEST_LOG'], 'a') as output:
    output.write(json.dumps(args) + '\n')
if os.environ.get('DOCKER_TEST_FAILURE') and os.environ['DOCKER_TEST_FAILURE'] in ' '.join(args):
    sys.exit(1)
if 'ps' in args:
    print('db\nredis\napi\nworker\nemail-worker\nbeat\nnginx')
""")
            binary.chmod(0o755)
            log = root / "calls.jsonl"
            environment = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                           "DOCKER_TEST_LOG": str(log), "DOCKER_TEST_FAILURE": failure}
            result = subprocess.run(["bash", str(root / "scripts/reset-demo.sh"), *arguments],
                                    input=confirmation, text=True, capture_output=True, env=environment)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_dry_run_does_not_stop_flush_or_reset(self):
        result, calls = self.run_wrapper("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("--dry-run" in call for call in calls))
        self.assertFalse(any("stop" in call or "FLUSHDB" in call or "--no-input" in call for call in calls))

    def test_cancellation_does_not_stop_services(self):
        result, calls = self.run_wrapper(confirmation="NO\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("stop" in call for call in calls))

    def test_success_orders_shutdown_flush_reset_and_restart(self):
        result, calls = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call.count("-f") == 1 and "compose.prod.yaml" in call for call in calls))
        operations = [call for call in calls if any(x in call for x in ["stop", "FLUSHDB", "--no-input", "up"])]
        self.assertEqual(len(operations), 6)
        self.assertEqual(operations[0][-1], "nginx")
        self.assertEqual(operations[1][-4:], ["api", "worker", "email-worker", "beat"])
        self.assertIn("FLUSHDB", operations[2])
        self.assertIn("--no-input", operations[3])
        self.assertNotIn("nginx", operations[4])
        self.assertEqual(operations[5][-1], "nginx")

    def test_preflight_failure_leaves_services_untouched(self):
        result, calls = self.run_wrapper(failure="reset_dev --dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stop" in call for call in calls))

    def test_shutdown_failure_never_flushes_or_resets(self):
        result, calls = self.run_wrapper(failure="stop --timeout 120 api")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("FLUSHDB" in call or "--no-input" in call or "up" in call for call in calls))

    def test_broker_cleanup_failure_does_not_reset_or_restart(self):
        result, calls = self.run_wrapper(failure="FLUSHDB")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("--no-input" in call or "up" in call for call in calls))

    def test_reset_failure_never_restarts_services(self):
        result, calls = self.run_wrapper(failure="reset_dev --no-input")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call for call in calls))

    def test_internal_restart_failure_keeps_proxy_closed(self):
        result, calls = self.run_wrapper(failure="--wait-timeout 120")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call and call[-1] == "nginx" for call in calls))

    def test_unknown_option_never_calls_docker(self):
        result, calls = self.run_wrapper("--yes")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
