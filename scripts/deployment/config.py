"""Validate literal deployment settings and stage only operation-required files."""

from pathlib import Path
import re
import shlex
import shutil
import stat
import sys


KEYS = {
    "DEPLOY_HOST", "DEPLOY_USER", "SSH_KEY_FILE", "SSH_KNOWN_HOSTS_FILE",
    "DEPLOY_PATH", "REPOSITORY", "API_DOMAIN", "CERTIFICATE_EMAIL",
    "APP_ENV_FILE", "GIT_KEY_FILE", "GIT_KNOWN_HOSTS_FILE", "ACTIONS_PUBLIC_KEY_FILE",
}


def read_settings(path):
    """Read KEY=value literals, rejecting duplicate keys and executable expansion."""
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in KEYS or key in values:
            raise ValueError(f"Invalid or duplicate deployment key on line {number}")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed literal on line {number}")
            value = value[1:-1]
        if any(character in value for character in "\r\n\0$`\\"):
            raise ValueError(f"Expansion and escape syntax are unsupported on line {number}")
        values[key] = value
    return values


def local_file(values, key, base, *, secret=False):
    """Resolve configuration-relative files and enforce private credential permissions."""
    path = Path(values.get(key, "")).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if any(character.isspace() or character in '"\'' for character in str(path)):
        raise ValueError(f"{key} path cannot contain whitespace or quote characters")
    if not path.is_file():
        raise ValueError(f"Set {key} to an existing file")
    if secret and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"Restrict {key} permissions with chmod 600")
    values[key] = str(path)
    return path


def prepare(operation, config, output):
    """Build a private SSH payload without executing deployment or application values."""
    values = read_settings(config)
    patterns = {
        "DEPLOY_HOST": r"[a-zA-Z0-9][a-zA-Z0-9.-]*",
        "DEPLOY_USER": r"[a-z_][a-z0-9_-]*",
        "DEPLOY_PATH": r"/[a-zA-Z0-9_./-]+",
        "API_DOMAIN": r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
    }
    for key, pattern in patterns.items():
        if not re.fullmatch(pattern, values.get(key, "")):
            raise ValueError(f"Set a valid {key}")
    path = Path(values["DEPLOY_PATH"])
    if ".." in path.parts or path == Path("/") or str(path) != values["DEPLOY_PATH"]:
        raise ValueError("DEPLOY_PATH must be a normalized absolute non-root path")
    for key in ("SSH_KEY_FILE", "SSH_KNOWN_HOSTS_FILE"):
        local_file(values, key, config.parent, secret=key == "SSH_KEY_FILE")
    if operation == "bootstrap":
        if not re.fullmatch(r"git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", values.get("REPOSITORY", "")):
            raise ValueError("REPOSITORY must be a GitHub SSH URL ending in .git")
        if not re.fullmatch(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", values.get("CERTIFICATE_EMAIL", "")):
            raise ValueError("Set CERTIFICATE_EMAIL")
        for key, name, secret in (
            ("APP_ENV_FILE", "app.env", True),
            ("GIT_KEY_FILE", "git-key", True),
            ("GIT_KNOWN_HOSTS_FILE", "git-known-hosts", False),
        ):
            shutil.copyfile(local_file(values, key, config.parent, secret=secret), output / name)
        if values.get("ACTIONS_PUBLIC_KEY_FILE"):
            public = local_file(values, "ACTIONS_PUBLIC_KEY_FILE", config.parent).read_text().strip()
            if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?", public):
                raise ValueError("ACTIONS_PUBLIC_KEY_FILE must contain one plain Ed25519 public key")
            (output / "actions.pub").write_text(public + "\n")
    local_keys = ("DEPLOY_HOST", "DEPLOY_USER", "SSH_KEY_FILE", "SSH_KNOWN_HOSTS_FILE", "API_DOMAIN")
    remote_keys = ("DEPLOY_USER", "DEPLOY_PATH", "REPOSITORY", "API_DOMAIN", "CERTIFICATE_EMAIL")
    for filename, keys in (("local.conf", local_keys), ("remote.conf", remote_keys)):
        content = [f"{key}={shlex.quote(values.get(key, ''))}" for key in keys]
        if filename == "remote.conf":
            content.append(f"operation={shlex.quote(operation)}")
        (output / filename).write_text("\n".join(content) + "\n")


if __name__ == "__main__":
    try:
        prepare(sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]))
    except (ValueError, OSError) as error:
        # Never include file contents in diagnostic output.
        print(f"Deployment configuration rejected: {error}", file=sys.stderr)
        sys.exit(2)
