"""``python -m phoenix.api``: loopback-only backend entrypoint (PHX-100, ADR-084).

The launcher refuses non-loopback hosts, binds the socket itself (port 0 asks the
OS for a free port), serves the composition root with uvicorn and, once startup
has finished, writes exactly one JSON line to stdout for the desktop shell:
``{"event": "phoenix.backend.ready", "host": ..., "port": ..., "pid": ...,
"version": ...}``. Logs go to stderr; the access log and proxy headers are off.
SIGINT, SIGTERM and Windows CTRL_BREAK shut down gracefully, cancelling active
missions first.
"""

import argparse
import json
import logging
import os
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TextIO

import uvicorn
from fastapi import FastAPI

from phoenix.api.server import (
    ProviderFactory,
    ServerConfig,
    build_server_app,
    cancel_active_missions,
    get_services,
)
from phoenix.core.config import AppSettings

LOOPBACK_HOSTS = ("127.0.0.1", "::1")
DEFAULT_PORT = 0
READY_EVENT = "phoenix.backend.ready"
GRACEFUL_SHUTDOWN_SECONDS = 10
EXIT_USAGE = 2


class LaunchError(Exception):
    """A launch refusal whose message is safe to print."""


@dataclass(frozen=True)
class LaunchOptions:
    host: str
    port: int
    data_dir: Path | None


def require_loopback(host: str) -> str:
    """Only literal loopback addresses; names such as localhost are refused."""
    if host not in LOOPBACK_HOSTS:
        raise LaunchError(
            f"Refusing to bind {host!r}: PHOENIX binds loopback only "
            "(--host 127.0.0.1 or --host ::1)"
        )
    return host


def _port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be 0-65535 (0 = OS-assigned)")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m phoenix.api",
        description="Run the PHOENIX backend on a loopback address.",
    )
    parser.add_argument("--host", default=LOOPBACK_HOSTS[0], help="127.0.0.1 or ::1")
    parser.add_argument(
        "--port", type=_port, default=DEFAULT_PORT, help="0 = OS-assigned free port"
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser


def parse_options(argv: Sequence[str] | None = None) -> LaunchOptions:
    args = build_parser().parse_args(argv)
    return LaunchOptions(
        host=require_loopback(args.host), port=args.port, data_dir=args.data_dir
    )


def load_settings(options: LaunchOptions) -> AppSettings:
    if options.data_dir is None:
        return AppSettings()
    return AppSettings(data_dir=options.data_dir)


def bind_loopback(host: str, port: int) -> socket.socket:
    """Bind before serving so the real port is known; exclusive on Windows."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        sock.bind((host, port))
    except BaseException:
        sock.close()
        raise
    return sock


def ready_line(host: str, port: int) -> str:
    return json.dumps(
        {
            "event": READY_EVENT,
            "host": host,
            "port": port,
            "pid": os.getpid(),
            "version": version("phoenix"),
        },
        separators=(",", ":"),
    )


class PhoenixServer(uvicorn.Server):
    """Announces readiness once and cancels missions before draining."""

    def __init__(
        self, config: uvicorn.Config, *, app: FastAPI, host: str, stdout: TextIO
    ) -> None:
        super().__init__(config)
        self._app = app
        self._host = host
        self._stdout = stdout
        self.announced_port: int | None = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started and not self.should_exit and self.announced_port is None:
            port = int(self.servers[0].sockets[0].getsockname()[1])
            self._stdout.write(ready_line(self._host, port) + "\n")
            self._stdout.flush()
            self.announced_port = port

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        services = get_services(self._app)
        if services is not None:
            await cancel_active_missions(services)
        await super().shutdown(sockets)


def create_server(
    options: LaunchOptions,
    *,
    settings: AppSettings,
    stdout: TextIO,
    config: ServerConfig | None = None,
    provider_factory: ProviderFactory | None = None,
) -> tuple[PhoenixServer, socket.socket]:
    sock = bind_loopback(options.host, options.port)
    app = build_server_app(settings, config=config, provider_factory=provider_factory)
    server = PhoenixServer(
        uvicorn.Config(
            app,
            lifespan="on",
            access_log=False,
            log_config=None,
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        ),
        app=app,
        host=options.host,
        stdout=stdout,
    )
    return server, sock


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    try:
        options = parse_options(argv)
    except LaunchError as error:
        err.write(f"phoenix.api: {error}\n")
        return EXIT_USAGE
    logging.basicConfig(
        stream=err, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    server, sock = create_server(options, settings=load_settings(options), stdout=out)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass  # uvicorn re-raises the captured signal after a graceful shutdown
    finally:
        sock.close()
    return 0 if server.started else 1
