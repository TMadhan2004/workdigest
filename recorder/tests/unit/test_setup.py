"""The setup wizard's API and local server: every step, with fakes for the cloud and the Mac."""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tests.conftest import IST
from tests.digest.test_onboarding import TEAMMATE
from workdigest import config_write
from workdigest.cloud.client import CloudError
from workdigest.config import load_config
from workdigest.integrations.http import HttpError
from workdigest.integrations.sync import Clients
from workdigest.llm import health
from workdigest.paths import Paths
from workdigest.secrets import SecretStore
from workdigest.service import ServiceStatus
from workdigest.setup.api import Deps, SetupApi, SetupError, find_repos
from workdigest.setup.server import SetupServer
from workdigest.setup.team import Team

TEAM = Team(
    cloud_url="https://demo.supabase.co", publishable_key="pk_demo", site_url="https://wd.example"
)


class FakeAuth:
    """Sign-up needs email confirmation; sign-in works once ``confirmed`` is set."""

    def __init__(self, secrets: SecretStore) -> None:
        self.secrets = secrets
        self.confirmed = False
        self.calls: list[str] = []

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        self.calls.append(f"up:{email}")
        return {"user": {"email": email}}

    def sign_in(self, email: str, password: str) -> object:
        self.calls.append(f"in:{email}")
        if not self.confirmed:
            raise CloudError("Email not confirmed")
        self.secrets.set("supabase-refresh-token", "r-1")
        return object()


class FakeAgent:
    """Records service installs instead of calling launchctl."""

    installed: ClassVar[list[Path]] = []

    def __init__(self, _paths: Paths) -> None:
        pass

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            bool(self.installed), bool(self.installed), 42 if self.installed else None, None
        )

    def install(self, executable: Path) -> Path:
        self.installed.append(executable)
        return executable


class Jira:
    def myself(self) -> dict[str, str]:
        return {"displayName": "Arjun K"}


class Bitbucket:
    def user(self) -> dict[str, str]:
        raise HttpError(401, "https://api.bitbucket.org/2.0/user", "unauthorized")


@pytest.fixture
def api(paths: Paths, tmp_path: Path) -> SetupApi:
    secrets = SecretStore()
    auth = FakeAuth(secrets)
    FakeAgent.installed = []
    deps = Deps(
        secrets=secrets,
        auth=lambda _team: auth,  # type: ignore[arg-type, return-value]
        git=lambda *args: {"user.name": "Arjun K", "user.email": "arjun.k@ei.study"}[args[-1]],
        prober=lambda name: health.Capability(
            "ok" if name == "cursor_cli" else "missing", "", "now"
        ),
        clients=lambda _c, s: Clients(
            jira=Jira() if s.get("jira-token") else None,  # type: ignore[arg-type]
            bitbucket=Bitbucket() if s.get("bitbucket-token") else None,  # type: ignore[arg-type]
        ),
        agent=FakeAgent,  # type: ignore[arg-type]
        executable=lambda: Path("/opt/digest"),
        claude_settings=tmp_path / "claude.json",
        cursor_hooks=tmp_path / "cursor.json",
        hook_python=lambda: "/usr/bin/python3",
        clock=lambda: datetime(2026, 10, 26, 10, 0, tzinfo=IST),
    )
    return SetupApi(paths, deps, TEAM)


def test_account_signup_confirm_and_domain(api: SetupApi) -> None:
    """TC-SET-001: Only company emails sign up; after the email link, "I've confirmed" signs in.

    Given: the team domain ei.study and a sign-up that needs email confirmation.
    When: a gmail address, a short password, then a valid sign-up, an early and a later "confirmed".
    Then: the first two are refused with clear messages; sign-up asks to confirm; "confirmed"
          fails until the link is opened, then signs in; config has the cloud URL, email, device id.
    """
    with pytest.raises(SetupError, match=r"@ei\.study"):
        api.account({"email": "me@gmail.com", "password": "longenough"})
    with pytest.raises(SetupError, match="8 characters"):
        api.account({"email": "arjun.k@ei.study", "password": "short"})
    assert (
        api.account({"email": "Arjun.K@ei.study", "password": "longenough"})["status"]
        == "confirm_email"
    )
    with pytest.raises(SetupError, match="not confirmed"):
        api.confirmed({})
    api.deps.auth(TEAM).confirmed = True  # type: ignore[misc, union-attr]
    assert api.confirmed({})["status"] == "signed_in"
    config = load_config(api.paths.config_path)
    assert (config.cloud.url, config.cloud.email) == (
        "https://demo.supabase.co",
        "arjun.k@ei.study",
    )
    assert config.cloud.device_id.startswith("mac-")
    assert config.atlassian.email == "arjun.k@ei.study"
    assert api.state()["account"]["signed_in"]


def test_profile_and_state_defaults(api: SetupApi) -> None:
    """TC-SET-002: The profile step suggests git's name and email and saves the manager.

    Given: git config with name Arjun K and email arjun.k@ei.study.
    When: the state is read, an incomplete profile is sent, then a full one.
    Then: suggestions come from git; the incomplete one is refused; the full one is in config.
    """
    profile = api.state()["profile"]
    assert (profile["name"], profile["emails"]) == ("Arjun K", ["arjun.k@ei.study"])
    with pytest.raises(SetupError, match="manager"):
        api.profile({"name": "Arjun K", "emails": ["arjun.k@ei.study"]})
    api.profile(
        {
            "name": "Arjun K",
            "emails": ["arjun.k@ei.study"],
            "manager_name": "Sana",
            "team_members": ["Meera"],
        }
    )
    config = load_config(api.paths.config_path)
    assert (config.digest.manager_name, config.digest.team_members) == ("Sana", ["Meera"])


def test_conversation_preview_and_apply(api: SetupApi) -> None:
    """TC-SET-003: The uploaded conversation is previewed, then applied to template, rules and history.

    Given: a teammate's conversation file.
    When: previewed, then applied; and a file with no digests is sent.
    Then: the preview names Sana and four sections without saving; apply writes template.json,
          imports 2 digests and sets the manager; the bad file gets the format explanation.
    """
    preview = api.conversation_preview({"text": TEAMMATE})
    assert preview["manager_name"] == "Sana"
    assert len(preview["sections"]) == 4
    assert not (api.paths.home / "template.json").exists()
    counts = api.conversation_apply({"text": TEAMMATE})
    assert counts["digests"] == 2
    assert (api.paths.home / "template.json").exists()
    assert load_config(api.paths.config_path).digest.manager_name == "Sana"
    with pytest.raises(SetupError, match="Me:"):
        api.conversation_preview({"text": "just notes"})


def test_repos_scan_and_save(api: SetupApi, tmp_path: Path) -> None:
    """TC-SET-004: Repositories are found under a folder (not inside each other) and saved.

    Given: work/a and work/b/c are git repos, work/node_modules/x too, work/a/inner too.
    When: work is scanned and a and c are saved; a plain folder is also tried.
    Then: a and b/c are found (not node_modules, not a/inner); config lists both; the plain folder is refused.
    """
    for repo in ("work/a", "work/b/c", "work/node_modules/x", "work/a/inner"):
        (tmp_path / repo / ".git").mkdir(parents=True)
    found = api.repos_scan({"root": str(tmp_path / "work")})["repos"]
    assert found == [str(tmp_path / "work/a"), str(tmp_path / "work/b/c")]
    api.repos({"paths": found})
    assert [r.path for r in load_config(api.paths.config_path).repos] == [Path(p) for p in found]
    with pytest.raises(SetupError, match="not git"):
        api.repos({"paths": [str(tmp_path)]})
    assert find_repos(tmp_path / "missing") == []


def test_atlassian_tokens_go_to_keychain_and_are_checked(api: SetupApi) -> None:
    """TC-SET-005: Tokens are stored in the Keychain only, then each one is tested.

    Given: a working Jira token and a refused Bitbucket token.
    When: both are submitted.
    Then: Jira OK as Arjun K; Bitbucket explains the refusal; the state shows both stored but never their values.
    """
    result = api.atlassian(
        {"email": "arjun.k@ei.study", "jira_token": "jt-1", "bitbucket_token": "bt-1"}
    )
    assert result["jira"] == {"ok": True, "detail": "Arjun K"}
    assert not result["bitbucket"]["ok"]
    assert "scopes" in result["bitbucket"]["detail"]
    state = json.dumps(api.state())
    assert "jt-1" not in state
    assert "bt-1" not in state
    assert api.state()["atlassian"]["jira_token"] is True
    assert "jt-1" not in api.paths.config_path.read_text()


def test_ai_check_and_choice(api: SetupApi) -> None:
    """TC-SET-006: Both AIs are tried; the choice is saved and "none" means facts only.

    Given: Cursor works, Claude is not installed.
    When: the AIs are checked, then Cursor is chosen, then facts only, then an unknown value.
    Then: states ok / missing with the login command; config provider changes; llm off for none; unknown refused.
    """
    result = api.ai_check()
    assert (result["cursor_cli"]["state"], result["claude_cli"]["state"]) == ("ok", "missing")
    assert result["cursor_cli"]["login"] == "cursor-agent login"
    api.ai({"provider": "cursor_cli"})
    assert load_config(api.paths.config_path).digest.provider == "cursor_cli"
    api.ai({"provider": "none"})
    assert not load_config(api.paths.config_path).transmit.llm_enabled
    with pytest.raises(SetupError):
        api.ai({"provider": "gpt"})


def test_hooks_start_and_heartbeat(api: SetupApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-SET-007: Hooks install where agreed; start needs an account, then runs the service.

    Given: no account yet, then a signed-in account.
    When: Claude hooks are agreed (Cursor not), start is pressed before and after sign-in, and the heartbeat is read.
    Then: only Claude's file has hooks; start refuses without an account, then enables cloud and
          installs the service; a fresh heartbeat reads online, an old one offline.
    """
    assert api.hooks({"claude": True, "cursor": False}) == {"claude": True}
    assert api.state()["hooks"] == {"claude": True, "cursor": False}
    with pytest.raises(SetupError, match="account"):
        api.start()
    api.deps.secrets.set("supabase-refresh-token", "r-1")
    config_write.update(
        api.paths.config_path,
        {"cloud": {"url": TEAM.cloud_url, "publishable_key": "pk", "device_id": "mac-1"}},
    )
    assert api.start() == {"started": True}
    assert FakeAgent.installed == [Path("/opt/digest")]
    assert load_config(api.paths.config_path).cloud.enabled

    seen = {"value": "2026-10-26T09:59:30+05:30"}

    class Rest:
        def __init__(self, *_a: object) -> None:
            pass

        def select(self, _table: str, _params: dict[str, str]) -> list[dict[str, str]]:
            return [{"last_seen_at": seen["value"]}]

    monkeypatch.setattr("workdigest.setup.api.Rest", Rest)
    assert api.heartbeat()["online"]
    seen["value"] = "2026-10-26T09:00:00+05:30"
    assert not api.heartbeat()["online"]


def test_config_writer_round_trip(tmp_path: Path) -> None:
    """TC-SET-008: The config writer merges tables and repos, validates, and keeps the file private.

    Given: an existing config with identity and a custom setting.
    When: digest values and repos are merged in, then an invalid provider.
    Then: everything loads back with mode 600; the invalid value is refused and the file is unchanged.
    """
    path = tmp_path / "config.toml"
    path.write_text('[identity]\nname = "Arjun K"\n\n[day]\nboundary_hour = 5\n')
    config_write.update(path, {"digest": {"manager_name": 'Sana "S"'}}, repos=[{"path": "/w/a"}])
    config = load_config(path)
    assert (config.identity.name, config.day.boundary_hour, config.digest.manager_name) == (
        "Arjun K",
        5,
        'Sana "S"',
    )
    assert config.repos[0].path == Path("/w/a")
    assert path.stat().st_mode & 0o777 == 0o600
    before = path.read_text()
    with pytest.raises(ValueError, match="provider"):
        config_write.update(path, {"digest": {"provider": "gpt"}})
    assert path.read_text() == before


def _request(
    server: SetupServer,
    method: str,
    path: str,
    *,
    token: str | None,
    host: str | None = None,
    body: Any = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{server.port}"}
    if token is not None:
        headers["X-Setup-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_server_security_and_routing(api: SetupApi, tmp_path: Path) -> None:
    """TC-SET-009: The local server needs the one-time token and its own Host, and sends strict headers.

    Given: the wizard server on 127.0.0.1 with a built page.
    When: the page, the API with no token, a wrong token, a foreign Host, a valid call, a step
          error, a path escape and finish are requested.
    Then: page 200 with CSP and no-store; 401, 401, 403; 200 with state; 422 with the message;
          the escape serves the page, not the file; finish stops the server.
    """
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<html>setup</html>")
    (tmp_path / "secret.txt").write_text("nope")
    server = SetupServer(api, root=root)
    assert server.url.startswith(f"http://127.0.0.1:{server.port}/#token=")
    thread = threading.Thread(target=server.serve, kwargs={"poll_s": 0.05}, daemon=True)
    thread.start()
    try:
        status, headers, data = _request(server, "GET", "/", token=None)
        assert (status, data) == (200, b"<html>setup</html>")
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"
        assert _request(server, "GET", "/api/state", token=None)[0] == 401
        assert _request(server, "GET", "/api/state", token="wrong")[0] == 401
        assert (
            _request(server, "GET", "/api/state", token=server.token, host="evil.example")[0] == 403
        )
        status, _, data = _request(server, "GET", "/api/state", token=server.token)
        assert status == 200
        assert json.loads(data)["team"]["domain"] == "ei.study"
        status, _, data = _request(
            server, "POST", "/api/account", token=server.token, body={"email": "x@gmail.com"}
        )
        assert status == 422
        assert "@ei.study" in json.loads(data)["error"]
        assert _request(server, "GET", "/../secret.txt", token=None)[2] == b"<html>setup</html>"
        assert _request(server, "POST", "/api/finish", token=server.token, body={})[0] == 200
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        if thread.is_alive():
            api.finished = True
            thread.join(timeout=5)


def test_install_script_is_safe() -> None:
    """TC-SET-010: install.sh is valid shell, refuses non-macOS, never uses sudo and runs `digest setup`.

    Given: the repository's install.sh.
    When: it is checked with `bash -n` and read.
    Then: it parses; it checks Darwin, pins the version from version.json, has no sudo, and ends with setup.
    """
    script = Path(__file__).parents[3] / "install" / "install.sh"
    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0
    text = script.read_text()
    assert "Darwin" in text
    assert "version.json" in text
    assert "sudo" not in text.replace("never asks for sudo", "")
    assert text.rstrip().splitlines()[-1].strip().startswith('"$DIGEST" setup')


def test_update_reads_the_latest_release() -> None:
    """TC-SET-011: `digest update` compares versions numerically and reads version.json from the site.

    Given: versions 0.10.0 and 0.9.3, and a site serving version.json.
    When: they are compared and the release is read.
    Then: 0.10.0 is newer; the wheel URL comes from the JSON.
    """
    import io

    from workdigest.update_check import latest_release, version_tuple

    assert version_tuple("0.10.0") > version_tuple("0.9.3")
    assert version_tuple("1.2.0+5") == (1, 2, 0)
    payload = b'{"version": "0.2.0", "mac": {"wheel_url": "https://x/w.whl"}}'
    urls: list[str] = []

    def opener(url: str, timeout: int) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(payload)

    assert latest_release("https://wd.example/", opener)["mac"]["wheel_url"] == "https://x/w.whl"
    assert urls == ["https://wd.example/version.json"]
