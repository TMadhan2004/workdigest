"""A tiny local web server for the setup wizard (Python stdlib only).

* Bound to ``127.0.0.1`` on a random free port; nothing else on the network can reach it.
* A one-time token is created per run and put in the page URL's ``#fragment`` (never sent in
  requests, so it is not in logs or the Referer header). The page sends it back in
  ``X-Setup-Token`` on every API call; calls without it are refused.
* The ``Host`` header must be this address, which blocks DNS-rebinding pages.
* Strict security headers: the page may only load its own files and talk to this server.
* It stops when setup finishes, or after two idle hours.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from workdigest.setup.api import SetupApi, SetupError

MAX_BODY = 3_000_000
IDLE_LIMIT_S = 2 * 60 * 60
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def web_root() -> Path:
    """The built wizard pages inside the package (``make web`` puts them there)."""
    return Path(str(resources.files("workdigest.setup").joinpath("web")))


class SetupServer:
    """Serves the wizard pages and its API until setup is finished."""

    def __init__(self, api: SetupApi, root: Path | None = None, port: int = 0) -> None:
        """Bind to 127.0.0.1 (``port`` 0 picks a free one)."""
        self.api = api
        self.root = (root or web_root()).resolve()
        self.token = secrets.token_urlsafe(24)
        self.last_activity = time.monotonic()
        self.routes = api.routes()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        """The port actually bound."""
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        """What to open in the browser (the token rides in the fragment)."""
        return f"http://127.0.0.1:{self.port}/#token={self.token}"

    def serve(self, poll_s: float = 0.5) -> None:
        """Run until setup finishes or the page has been idle too long."""
        thread = threading.Thread(
            target=self.httpd.serve_forever, name="workdigest-setup", daemon=True
        )
        thread.start()
        try:
            while not self.api.finished and time.monotonic() - self.last_activity < IDLE_LIMIT_S:
                time.sleep(poll_s)
            time.sleep(poll_s)  # let the last response go out
        finally:
            self.httpd.shutdown()
            self.httpd.server_close()

    # ---------------------------------------------------------------- handler
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WorkDigestSetup"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                """Quiet: requests carry personal details."""

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def _dispatch(self, method: str) -> None:
                server.last_activity = time.monotonic()
                allowed_hosts = {f"127.0.0.1:{server.port}", f"localhost:{server.port}"}
                if self.headers.get("Host") not in allowed_hosts:
                    self._send(HTTPStatus.FORBIDDEN, b"wrong host", "text/plain")
                    return
                path = urlsplit(self.path).path
                if path.startswith("/api/"):
                    self._api(method, path)
                elif method == "GET":
                    self._static(path)
                else:
                    self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")

            def _api(self, method: str, path: str) -> None:
                token = self.headers.get("X-Setup-Token", "")
                if not hmac.compare_digest(token.encode(), server.token.encode()):
                    self._json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "this setup link has expired; run `digest setup` again"},
                    )
                    return
                action = server.routes.get((method, path))
                if action is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown step"})
                    return
                body: dict[str, Any] = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > MAX_BODY:
                        self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
                        return
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send JSON"})
                        return
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                        return
                try:
                    self._json(HTTPStatus.OK, action(body))
                except SetupError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001 - the page shows it; the server keeps running
                    self._json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"unexpected error: {type(exc).__name__}: {exc}"[:300]},
                    )

            def _static(self, path: str) -> None:
                target = (server.root / path.lstrip("/")).resolve()
                if not target.is_relative_to(server.root) or not target.is_file():
                    target = server.root / "index.html"  # the app's own routes
                if not target.is_file():
                    self._send(
                        HTTPStatus.NOT_FOUND,
                        b"The setup pages are not built. Run `make web` in the repository.",
                        "text/plain",
                    )
                    return
                kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self._send(HTTPStatus.OK, target.read_bytes(), kind)

            def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
                self._send(status, json.dumps(payload).encode(), "application/json")

            def _send(self, status: HTTPStatus, data: bytes, kind: str) -> None:
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    kind
                    + ("; charset=utf-8" if kind.startswith(("text/", "application/j")) else ""),
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.end_headers()
                self.wfile.write(data)

        return Handler
