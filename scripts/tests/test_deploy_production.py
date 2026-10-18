"""Verify release ordering and failure isolation with local Git and fake Docker."""

import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy-production.sh"


class DeployProductionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "backend"
        self.repository.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "ci@example.com")
        self.git("config", "user.name", "CI")
        (self.repository / "compose.prod.yaml").write_text("# Fake Docker only\n")
        (self.repository / ".gitignore").write_text(".env\n")
        (self.repository / ".env").write_text("KEEP=this-file\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial release")
        self.revision = self.git("rev-parse", "HEAD").strip()
        origin = self.root / "origin.git"
        subprocess.run(["git", "clone", "--bare", str(self.repository), str(origin)],
                       check=True, capture_output=True)
        self.git("remote", "add", "origin", str(origin))
        binary_directory = self.root / "bin"
        binary_directory.mkdir()
        docker = binary_directory / "docker"
        docker.write_text(r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as output:
    output.write(json.dumps(args) + '\n')
if os.environ.get('DEPLOY_TEST_FAILURE') and os.environ['DEPLOY_TEST_FAILURE'] in ' '.join(args):
    sys.exit(1)
if args[0] == 'inspect':
    print(os.environ.get('DEPLOY_TEST_HEALTH', 'healthy'))
elif 'ps' in args and '-q' in args:
    print(args[-1] + '-container')
elif 'ps' in args:
    print('db\nredis\napi\nworker\nemail-worker\nbeat')
''')
        docker.chmod(0o755)
        self.log = self.root / "docker.jsonl"
        self.environment = {
            **os.environ,
            "PATH": str(binary_directory) + os.pathsep + os.environ["PATH"],
            "TMPDIR": str(self.root),
            "DEPLOY_TEST_LOG": str(self.log),
        }

    def git(self, *arguments):
        """Run Git only in the disposable local checkout."""
        return subprocess.run(["git", *arguments], cwd=self.repository, check=True,
                              text=True, capture_output=True).stdout

    def deploy(self, *, revision=None, failure="", health="healthy"):
        """Run the real script with injected Docker failures, never real services."""
        result = subprocess.run(
            ["bash", "-s", "--", revision or self.revision, str(self.repository)],
            env={**self.environment, "DEPLOY_TEST_FAILURE": failure, "DEPLOY_TEST_HEALTH": health},
            input=SCRIPT.read_text(), capture_output=True, text=True,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_success_builds_then_stops_then_migrates_and_recreates(self):
        result, calls = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = [call for call in calls if any(x in call for x in ("build", "stop", "run", "up", "exec"))]
        self.assertEqual(operations[0][-2:], ["build", "api"])
        self.assertEqual(operations[1][-1], "nginx")
        self.assertEqual(operations[2][-4:], ["api", "worker", "email-worker", "beat"])
        self.assertEqual(operations[3][-1], "migrate")
        self.assertEqual(operations[4][-1], "collectstatic")
        self.assertEqual(operations[5][-4:], ["api", "worker", "email-worker", "beat"])
        self.assertIn("--force-recreate", operations[5])
        self.assertEqual(operations[7][-1], "nginx")
        self.assertEqual(operations[8][-2:], ["nginx", "-t"])
        self.assertTrue(all("--no-deps" in call for call in calls if "up" in call or "run" in call))
        self.assertFalse(any("down" in call or "FLUSHDB" in call for call in calls))
        self.assertEqual((self.repository / ".env").read_text(), "KEEP=this-file\n")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.revision)

    def test_release_retains_checkout_repository_ssh_identity(self):
        command = "ssh -i /dedicated/repository-key -o StrictHostKeyChecking=yes"
        self.git("config", "core.sshCommand", command)
        marker = self.root / "git-ssh-command"
        real_git = shutil.which("git")
        spy = self.root / "bin/git"
        spy.write_text("#!/usr/bin/env python3\nimport os, pathlib, sys\n"
                       "if sys.argv[1] == 'fetch':\n"
                       f"    pathlib.Path({str(marker)!r}).write_text(os.environ['GIT_SSH_COMMAND'])\n"
                       f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
        spy.chmod(0o755)
        result, calls = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), command + " -o BatchMode=yes")

    def test_superseded_commit_does_not_touch_services(self):
        (self.repository / "new-file").write_text("New release\n")
        self.git("add", ".")
        self.git("commit", "-m", "New release")
        self.git("push", "origin", "main")
        result, calls = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping superseded", result.stdout)
        self.assertEqual(calls, [])

    def test_dirty_checkout_is_not_discarded(self):
        for path in ("compose.prod.yaml", "untracked-file"):
            with self.subTest(path=path):
                target = self.repository / path
                target.write_text("Local changes\n")
                result, calls = self.deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertEqual(target.read_text(), "Local changes\n")
                if path == "compose.prod.yaml":
                    self.git("restore", path)

    def test_shared_reset_lock_blocks_deployment(self):
        key = hashlib.sha256(str(self.repository.resolve()).encode()).hexdigest()
        with (self.root / f"ecommerce-reset-{os.getuid()}-{key}.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, calls = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deployment or demo reset", result.stderr)
        self.assertEqual(calls, [])

    def test_unhealthy_infrastructure_leaves_application_alone(self):
        result, calls = self.deploy(health="unhealthy")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stop" in call or "build" in call for call in calls))

    def test_fetch_failure_does_not_touch_services(self):
        self.git("remote", "set-url", "origin", str(self.root / "missing-origin.git"))
        result, calls = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_build_failure_does_not_stop_services(self):
        result, calls = self.deploy(failure="build api")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stop" in call for call in calls))

    def test_shutdown_failure_does_not_run_migrations(self):
        result, calls = self.deploy(failure="stop --timeout 120 api")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("run" in call or "up" in call for call in calls))

    def test_migration_failure_keeps_traffic_stopped(self):
        result, calls = self.deploy(failure="--interactive=false migrate")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("collectstatic" in call or "up" in call for call in calls))
        self.assertEqual(calls[-1][-1], "nginx")

    def test_static_failure_does_not_restart_application(self):
        result, calls = self.deploy(failure="--interactive=false collectstatic")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call for call in calls))

    def test_api_startup_failure_does_not_start_nginx(self):
        result, calls = self.deploy(failure="--wait-timeout 180")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call and call[-1] == "nginx" for call in calls))

    def test_database_check_failure_does_not_start_nginx(self):
        result, calls = self.deploy(failure="connection.ensure_connection()")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call and call[-1] == "nginx" for call in calls))

    def test_nginx_validation_failure_stops_traffic_again(self):
        result, calls = self.deploy(failure="nginx nginx -t")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][-4:], ["stop", "--timeout", "30", "nginx"])

    def test_invalid_revision_does_not_touch_services(self):
        result, calls = self.deploy(revision="main; echo invalid")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
