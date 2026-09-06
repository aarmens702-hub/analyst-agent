"""The kernel process must not inherit the host's secrets (spec R9 sandbox).

`crivo` loads a .env into os.environ at startup, so the parent process holds
DEEPSEEK_API_KEY / ANTHROPIC_API_KEY for the whole session. Model-authored
code runs inside the kernel; if that kernel inherits the parent environment,
every key is one `os.environ` away from being printed into a transcript.

The end-to-end test below starts a real subprocess kernel with sentinel keys
planted in the parent environment and reads os.environ back out of the cell.
"""

import ast
import json
import sys

import pytest

from crivo.kernel.client import KernelClient, _child_env

SUBPROCESS_ARGV = [sys.executable, "-m", "crivo.kernel.supervisor"]

SENTINEL = "sentinel-value-that-must-not-reach-the-kernel"

SECRET_NAMES = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "CRIVO_API_KEY",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
)

SUSPICIOUS_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

PROBE = (
    "import json, os\n"
    f"sentinel = {SENTINEL!r}\n"
    f"markers = {SUSPICIOUS_MARKERS!r}\n"
    f"names = {SECRET_NAMES!r}\n"
    "json.dumps({\n"
    "    'present': [n for n in names if n in os.environ],\n"
    "    'suspicious': sorted(\n"
    "        k for k in os.environ if any(m in k.upper() for m in markers)\n"
    "    ),\n"
    "    'sentinel_in': sorted(k for k, v in os.environ.items() if sentinel in v),\n"
    "    'has_path': bool(os.environ.get('PATH')),\n"
    "})\n"
)


def _plant_secrets(monkeypatch) -> None:
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, SENTINEL)
    monkeypatch.setenv("MY_VENDOR_TOKEN", SENTINEL)


def test_kernel_cell_cannot_read_host_api_keys(tmp_path, monkeypatch):
    """A cell must see neither the named provider keys nor anything key-shaped."""
    _plant_secrets(monkeypatch)
    kc = KernelClient(workspace_dir=tmp_path / "ws", transport_argv=SUBPROCESS_ARGV)
    try:
        assert kc.start().proto == 1
        result = list(kc.execute(PROBE, timeout_s=120))[-1]
        assert result.status == "ok", result.error
        seen = json.loads(ast.literal_eval(result.value))
    finally:
        kc.close()

    assert seen["present"] == [], (
        f"named keys leaked into the kernel: {seen['present']}"
    )
    assert seen["suspicious"] == [], (
        f"key-shaped env vars leaked into the kernel: {seen['suspicious']}"
    )
    assert seen["sentinel_in"] == [], (
        f"the secret value leaked under other names: {seen['sentinel_in']}"
    )
    assert seen["has_path"], "the kernel still needs PATH to run"


def test_child_env_keeps_startup_vars_and_drops_the_rest(monkeypatch):
    """The allowlist carries what the supervisor needs and nothing extra."""
    _plant_secrets(monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/analyst")
    monkeypatch.setenv("SOME_UNRELATED_HOST_VAR", "x")

    env = _child_env()

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/analyst"
    for name in SECRET_NAMES:
        assert name not in env
    assert "MY_VENDOR_TOKEN" not in env
    assert "SOME_UNRELATED_HOST_VAR" not in env


def test_child_env_omits_unset_vars(monkeypatch):
    """An allowlisted name that is unset stays unset, not empty-string set."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert "PYTHONPATH" not in _child_env()


@pytest.mark.parametrize("name", SUSPICIOUS_MARKERS)
def test_no_allowlisted_name_is_key_shaped(name):
    """A name check on the allowlist, and no more than that: it would not
    notice DOCKER_CONFIG (registry credentials) or a proxy URL carrying
    user:password, so it is a spelling rule, not a guarantee."""
    from crivo.kernel.client import KERNEL_ENV_PASSTHROUGH

    assert not [k for k in KERNEL_ENV_PASSTHROUGH if name in k.upper()]


def test_the_kernel_keeps_the_operators_local_time(monkeypatch):
    """TZ is not key-shaped and the kernel needs it: without it datetime.now()
    inside a cell resolves against a different zone from the CLI printing the
    answer, which is a silent wrong number, not a failure."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")

    assert _child_env()["TZ"] == "Asia/Tokyo"


def test_the_kernel_keeps_the_operators_reader_tuning(monkeypatch):
    """crivo.read(url) from a cell reads these at call time. Dropped, the cell
    silently used the defaults while remote.py's own refusal told the user to
    raise a limit that no longer reached it."""
    from crivo.kernel.client import KERNEL_ENV_PASSTHROUGH
    from crivo.readers.remote import READER_ENV_VARS

    monkeypatch.setenv("CRIVO_HTTP_MAX_BYTES", "50000000")
    monkeypatch.setenv("CRIVO_HTTP_TIMEOUT_S", "120")

    env = _child_env()
    assert env["CRIVO_HTTP_MAX_BYTES"] == "50000000"
    assert env["CRIVO_HTTP_TIMEOUT_S"] == "120"
    assert set(READER_ENV_VARS) <= set(KERNEL_ENV_PASSTHROUGH), (
        "a reader tunable the operator can set must reach the cell that reads it"
    )


def test_docker_credentials_reach_the_docker_cli_and_not_a_host_cell(monkeypatch):
    """In docker mode the child is the docker CLI and needs to find and
    authenticate to the daemon; in subprocess mode the child is the process
    model-authored code runs in, and DOCKER_CONFIG (registry credentials),
    DOCKER_CERT_PATH (a TLS client key) and SSH_AUTH_SOCK (an agent that
    signs) have no business in it."""
    for name in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, "/host/thing")

    subprocess_env = _child_env()
    docker_env = _child_env(docker=True)

    for name in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"):
        assert name not in subprocess_env, name
        assert docker_env[name] == "/host/thing", name
