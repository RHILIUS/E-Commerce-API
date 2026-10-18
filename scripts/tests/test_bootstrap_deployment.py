"""Exercise deployment boundaries without contacting a VM or running containers."""

import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deployment_config", ROOT / "deployment/config.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "payload"
        self.output.mkdir()
        for name in ("vm-key", "hosts", "app.env", "git-key", "git-hosts"):
            path = self.root / name
            path.write_text("SECRET_SENTINEL\n")
            path.chmod(0o600)
        self.settings = {
            "DEPLOY_HOST": "203.0.113.1", "DEPLOY_USER": "ubuntu",
            "SSH_KEY_FILE": "vm-key", "SSH_KNOWN_HOSTS_FILE": "hosts",
            "DEPLOY_PATH": "/home/ubuntu/ecommerce/backend", "API_DOMAIN": "api.example.com",
            "REPOSITORY": "git@github.com:example/backend.git", "CERTIFICATE_EMAIL": "ops@example.com",
            "APP_ENV_FILE": "app.env", "GIT_KEY_FILE": "git-key", "GIT_KNOWN_HOSTS_FILE": "git-hosts",
        }
        self.path = self.root / ".env.deploy"

    def prepare(self, operation="bootstrap", extra=""):
        """Stage literals through the same parser used by the public wrapper."""
        self.path.write_text("\n".join(f"{key}={value}" for key, value in self.settings.items()) + "\n" + extra)
        config.prepare(operation, self.path, self.output)

    def test_bootstrap_stages_only_required_secrets(self):
        self.prepare()
        self.assertEqual((self.output / "app.env").read_text(), "SECRET_SENTINEL\n")
        self.assertNotIn("SECRET_SENTINEL", (self.output / "remote.conf").read_text())
        self.assertNotIn("SSH_KEY_FILE", (self.output / "remote.conf").read_text())
        self.assertEqual((self.output / "git-key").read_text(), "SECRET_SENTINEL\n")

    def test_check_update_status_never_stage_application_or_repository_keys(self):
        for operation in ("check", "update", "status"):
            with self.subTest(operation=operation):
                self.prepare(operation)
                self.assertEqual({p.name for p in self.output.iterdir()}, {"local.conf", "remote.conf"})

    def test_rejects_duplicate_unknown_and_executable_values(self):
        for extra in ("DEPLOY_USER=root", "UNKNOWN=value", 'CERTIFICATE_EMAIL=$(id)', 'API_DOMAIN=`id`'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.prepare(extra=extra)

    def test_rejects_unsafe_hosts_paths_and_repository(self):
        for key, value in (("DEPLOY_HOST", "-oProxyCommand=id"), ("DEPLOY_PATH", "/tmp/../etc"),
                           ("DEPLOY_PATH", "/"), ("DEPLOY_PATH", "/tmp/path with spaces"),
                           ("API_DOMAIN", "*.example.com"), ("REPOSITORY", "https://token@github.com/x/y")):
            original = self.settings[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.settings[key] = value
                self.prepare()
            self.settings[key] = original

    def test_private_files_must_have_restricted_permissions(self):
        for name in ("vm-key", "app.env", "git-key"):
            path = self.root / name
            path.chmod(0o644)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare()
            path.chmod(0o600)

    def run_wrapper(self, operation, ssh_failure=False):
        """Capture SSH/tar transport locally, with no real network or credential use."""
        self.prepare(operation)
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        log = self.root / "transport.jsonl"
        if log.exists():
            log.unlink()
        for name in ("ssh", "ssh-keygen", "curl"):
            executable = binary / name
            executable.write_text('''#!/usr/bin/env python3
import io, json, os, pathlib, sys, tarfile
name = pathlib.Path(sys.argv[0]).name
record = {'name': name, 'arguments': sys.argv[1:]}
if name == 'ssh':
    data = sys.stdin.buffer.read()
    record['files'] = tarfile.open(fileobj=io.BytesIO(data)).getnames()
with open(os.environ['TRANSPORT_LOG'], 'a') as log: log.write(json.dumps(record) + '\\n')
if name == 'ssh' and os.environ.get('SSH_FAIL'): sys.exit(1)
''')
            executable.chmod(0o755)
        result = subprocess.run(["bash", str(ROOT / "deploy.sh"), operation, "--env-file", str(self.path)],
                                env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                     "TRANSPORT_LOG": str(log), "SSH_FAIL": "1" if ssh_failure else ""},
                                capture_output=True, text=True)
        return result, [json.loads(line) for line in log.read_text().splitlines()]

    def test_wrapper_preserves_verified_ssh_and_limits_uploaded_secrets(self):
        for operation in ("check", "bootstrap", "update", "status"):
            with self.subTest(operation=operation):
                result, calls = self.run_wrapper(operation)
                self.assertEqual(result.returncode, 0, result.stderr)
                ssh = next(call for call in calls if call["name"] == "ssh")
                self.assertIn("StrictHostKeyChecking=yes", ssh["arguments"])
                self.assertIn("BatchMode=yes", ssh["arguments"])
                self.assertNotIn("./local.conf", ssh["files"])
                self.assertNotIn("./vm-key", ssh["files"])
                self.assertEqual("./app.env" in ssh["files"], operation == "bootstrap")
                self.assertEqual(any(call["name"] == "curl" for call in calls), operation in ("bootstrap", "update"))
                # Clear the helper's staged files so each operation is independent.
                for path in self.output.iterdir():
                    path.unlink()

    def test_wrapper_never_verifies_public_success_after_ssh_failure(self):
        result, calls = self.run_wrapper("bootstrap", ssh_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["name"] == "curl" for call in calls))

    def test_actions_key_must_be_one_plain_public_key(self):
        self.settings["ACTIONS_PUBLIC_KEY_FILE"] = "actions.pub"
        public = self.root / "actions.pub"
        public.write_text('command="arbitrary" ssh-ed25519 AAAA')
        with self.assertRaises(ValueError):
            self.prepare()
        public.write_text("ssh-ed25519 AAAA actions\n")
        self.prepare()
        self.assertEqual((self.output / "actions.pub").read_text(), "ssh-ed25519 AAAA actions\n")


class BootstrapOrderingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "backend"
        self.repository.mkdir()
        self.payload = self.root / "payload"
        self.payload.mkdir()
        self.binary = self.root / "bin"
        self.binary.mkdir()
        self.log = self.root / "calls.jsonl"
        self.environment = {**os.environ, "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
                            "DEPLOY_TEST_LOG": str(self.log), "HOME": str(self.root / "home")}
        self.os_release = self.root / "os-release"
        self.os_release.write_text('ID=ubuntu\nVERSION_ID=24.04\n')
        # Substitute host-only preflight reads; all deployment control flow remains real.
        remote = (ROOT / "deployment/remote.sh").read_text().replace("/etc/os-release", str(self.os_release))
        remote = remote.replace("/proc/meminfo", str(self.root / "meminfo"))
        (self.root / "meminfo").write_text("MemTotal: 4000000 kB\n")
        self.script = self.root / "remote.sh"
        self.script.write_text(remote)
        for name in ("git", "docker", "sudo", "df", "getent", "curl", "ssh-keygen"):
            executable = self.binary / name
            executable.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
call = name + ' ' + ' '.join(args)
if os.environ.get('DEPLOY_TEST_FAILURE') and os.environ['DEPLOY_TEST_FAILURE'] in call:
    sys.exit(1)
if name == 'df': print('Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 99999999 0 99999999 0% /')
if name == 'git':
    if args[:3] == ['remote', 'get-url', 'origin']: print('git@github.com:example/backend.git')
    if args[:1] == ['rev-parse']: print('a' * 40)
    if args[:1] == ['ls-remote']: print('a' * 40 + '\\trefs/heads/main')
    if args[:1] == ['show']: print('exit 0')
if name == 'docker' and 'ps' in args and '-a' in args: print('db redis')
if name == 'sudo' and 'test' in args: sys.exit(1 if os.environ.get('DEPLOY_TEST_NO_CERT') else 0)
''')
            executable.chmod(0o755)
        self.write_operation("status")

    def write_operation(self, operation):
        """Supply trusted generated remote configuration to the real shell flow."""
        values = {"operation": operation, "DEPLOY_USER": pwd.getpwuid(os.getuid()).pw_name,
                  "DEPLOY_PATH": str(self.repository), "API_DOMAIN": "api.example.com",
                  "REPOSITORY": "git@github.com:example/backend.git", "CERTIFICATE_EMAIL": "ops@example.com"}
        (self.payload / "remote.conf").write_text("\n".join(f"{k}={shlex.quote(v)}" for k, v in values.items()))

    def run_remote(self, failure=""):
        """Run fake host commands with no real sudo, Docker, Git fetch or network."""
        result = subprocess.run(["bash", str(self.script), str(self.payload), "--prepared"],
                                env={**self.environment, "DEPLOY_TEST_FAILURE": failure}, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_check_has_no_host_mutations(self):
        self.write_operation("check")
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] in ("git", "docker", "curl") for call in calls))
        self.assertEqual([c for c in calls if c[0] == "sudo"], [["sudo", "-n", "true"]])

    def test_unsupported_os_fails_before_sudo(self):
        self.os_release.write_text('ID=debian\nVERSION_ID=12\n')
        result, calls = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_status_never_builds_stops_or_migrates(self):
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(word in call for call in calls for word in ("build", "stop", "run", "up", "fetch")))

    def test_update_requires_completed_bootstrap(self):
        self.write_operation("update")
        result, calls = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "git" for call in calls))

    def test_update_delegates_selected_revision_without_unlocked_fetch(self):
        self.write_operation("update")
        (self.repository / ".bootstrap-state").mkdir()
        (self.repository / ".bootstrap-state/complete").touch()
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["git", "ls-remote", "origin", "refs/heads/main"], calls)
        self.assertIn(["git", "show", "a" * 40 + ":scripts/deploy-production.sh"], calls)
        self.assertFalse(any("fetch" in call for call in calls))

    def prepare_bootstrap(self):
        """Set up the clone contract; all external and privileged actions stay fake."""
        self.write_operation("bootstrap")
        (self.repository / ".git").mkdir()
        (self.repository / "scripts/deployment").mkdir(parents=True)
        (self.repository / "scripts/deployment/renew-certificate.sh").touch()
        (self.repository / "compose.prod.yaml").write_text("# ./deploy/acme\n")
        (self.repository / "deploy").mkdir()
        for name in ("git-key", "git-known-hosts", "app.env"):
            (self.payload / name).write_text("KEEP_ME\n")
        (self.payload / "renew-certificate.sh").write_text((ROOT / "deployment/renew-certificate.sh").read_text())
        key = hashlib.sha256(str(self.repository).encode()).hexdigest()
        self.addCleanup(Path(f"/tmp/ecommerce-reset-{os.getuid()}-{key}.lock").unlink, missing_ok=True)

    def test_initial_issuance_uses_webroot_and_cleans_challenge_container(self):
        self.prepare_bootstrap()
        self.environment["DEPLOY_TEST_NO_CERT"] = "1"
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        issuance = next(call for call in calls if "certonly" in call)
        self.assertIn("--webroot", issuance)
        self.assertIn(str(self.repository / "deploy/acme"), issuance)
        self.assertIn(["docker", "rm", "-f", "ecommerce-bootstrap-http"], calls)

    def test_issuance_failure_never_starts_database_or_migrations(self):
        self.prepare_bootstrap()
        self.environment["DEPLOY_TEST_NO_CERT"] = "1"
        result, calls = self.run_remote("certbot certonly")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repository / ".bootstrap-state/complete").exists())
        self.assertFalse(any("up" in call or call[-1] == "migrate" for call in calls))
        self.assertEqual(calls[-1], ["docker", "rm", "-f", "ecommerce-bootstrap-http"])

    def test_bootstrap_success_and_completed_rerun_preserve_state(self):
        self.prepare_bootstrap()
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repository / ".bootstrap-state/complete").exists())
        operations = [call for call in calls if call[0] == "docker"]
        migration = next(i for i, call in enumerate(operations) if call[-1] == "migrate")
        nginx = next(i for i, call in enumerate(operations) if "up" in call and call[-1] == "nginx")
        self.assertLess(migration, nginx)
        self.log.unlink()
        result, calls = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(word in call for call in calls for word in ("build", "stop", "run", "up")))
        self.assertEqual((self.repository / ".env").read_text(), "KEEP_ME\n")

    def test_bootstrap_failures_block_success_and_keep_traffic_closed(self):
        for failure in ("docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml build api",
                        "--interactive=false migrate", "--interactive=false collectstatic",
                        "--wait-timeout 180 api", "connection.ensure_connection()", "nginx nginx -t"):
            with self.subTest(failure=failure):
                # A fresh isolated fixture per failure also verifies partial reruns separately.
                fixture = BootstrapOrderingTests()
                fixture.setUp()
                try:
                    fixture.prepare_bootstrap()
                    result, calls = fixture.run_remote(failure)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((fixture.repository / ".bootstrap-state/complete").exists())
                    self.assertEqual((fixture.repository / ".env").read_text(), "KEEP_ME\n")
                    if "build api" not in failure:
                        self.assertEqual(calls[-1][-4:], ["stop", "--timeout", "30", "nginx"])
                    else:
                        self.assertFalse(any("stop" in call for call in calls))
                    fixture.log.unlink()
                    result, calls = fixture.run_remote()
                    self.assertEqual(result.returncode, 0, result.stderr)
                finally:
                    fixture.doCleanups()

    def test_conflicting_existing_environment_is_never_overwritten(self):
        self.prepare_bootstrap()
        (self.repository / ".env").write_text("ORIGINAL\n")
        result, calls = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repository / ".env").read_text(), "ORIGINAL\n")
        self.assertFalse(any("build" in call or "stop" in call for call in calls))

    def test_shared_lock_blocks_bootstrap_before_credentials_or_containers(self):
        self.write_operation("bootstrap")
        key = hashlib.sha256(str(self.repository).encode()).hexdigest()
        lock_path = Path(f"/tmp/ecommerce-reset-{os.getuid()}-{key}.lock")
        self.addCleanup(lock_path.unlink, missing_ok=True)
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, calls = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another deployment", result.stderr)
        self.assertFalse(any(call[0] == "git" or "up" in call for call in calls))


class RenewalHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "deploy/certs").mkdir(parents=True)
        lineage = self.root / "lineage"
        lineage.mkdir()
        (lineage / "fullchain.pem").write_text("new-chain")
        (lineage / "privkey.pem").write_text("new-key")
        self.script = self.root / "hook.sh"
        text = (ROOT / "deployment/renew-certificate.sh").read_text()
        text = text.replace("__REPOSITORY__", str(self.root)).replace("__DEPLOY_USER__", "unused")
        text = text.replace("__LOCK__", str(self.root / "lock"))
        self.script.write_text(text.replace("/etc/letsencrypt/live/ecommerce-backend", str(lineage)))
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text('''#!/usr/bin/env python3
import os, sys
with open(os.environ['HOOK_LOG'], 'a') as log: log.write(' '.join(sys.argv[1:]) + '\\n')
if 'ps' in sys.argv: print('nginx-container')
if os.environ.get('HOOK_FAIL') and sys.argv[-1] == '-t': sys.exit(1)
''')
        docker.chmod(0o755)
        self.log = self.root / "calls"
        self.environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "HOOK_LOG": str(self.log)}

    def test_success_replaces_files_then_tests_and_reloads_nginx(self):
        result = subprocess.run(["bash", str(self.script), "--locked"], env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "deploy/certs/fullchain.pem").read_text(), "new-chain")
        private = self.root / "deploy/certs/privkey.pem"
        self.assertEqual(private.read_text(), "new-key")
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        calls = self.log.read_text()
        self.assertLess(calls.index("nginx -t"), calls.index("nginx -s reload"))

    def test_failed_nginx_validation_never_reloads(self):
        result = subprocess.run(["bash", str(self.script), "--locked"],
                                env={**self.environment, "HOOK_FAIL": "1"}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("nginx -s reload", self.log.read_text())

    def test_other_lineages_are_ignored(self):
        result = subprocess.run(["bash", str(self.script)],
                                env={**self.environment, "RENEWED_LINEAGE": "/different"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
