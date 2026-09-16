"""Run the loopback-only Studio API on its product port."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

import uvicorn
from studio_runtime.agents import AGENT_SERVING_PORT_MAX, AGENT_SERVING_PORT_MIN


DEFAULT_STUDIO_PORT = 24368


def server_options(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    values = os.environ if environ is None else environ
    return {
        "host": values.get("STUDIO_HOST", "127.0.0.1"),
        "port": int(values.get("STUDIO_PORT", DEFAULT_STUDIO_PORT)),
    }


def agent_serving_ports(environ: Mapping[str, str] | None = None) -> range:
    values = os.environ if environ is None else environ
    first = int(values.get("STUDIO_AGENT_PORT_MIN", AGENT_SERVING_PORT_MIN))
    last = int(values.get("STUDIO_AGENT_PORT_MAX", AGENT_SERVING_PORT_MAX))
    if first < 1024 or last > 65535 or first > last:
        raise ValueError("The Agent serving port range is invalid.")
    return range(first, last + 1)


def listening_ports(environ: Mapping[str, str] | None = None) -> list[int]:
    studio_port = int(server_options(environ)["port"])
    ports = [studio_port, *agent_serving_ports(environ)]
    if len(ports) != len(set(ports)):
        raise ValueError("The Studio port must not overlap the Agent serving port range.")
    return ports


def server_sockets(environ: Mapping[str, str] | None = None) -> list[socket.socket]:
    options = server_options(environ)
    host = str(options["host"])
    sockets: list[socket.socket] = []
    try:
        for port in listening_ports(environ):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.setblocking(False)
            sockets.append(listener)
    except Exception:
        for listener in sockets:
            listener.close()
        raise
    return sockets


def main() -> None:
    options = server_options()
    server = uvicorn.Server(uvicorn.Config("studio_api.main:app", **options))
    server.run(sockets=server_sockets())


if __name__ == "__main__":
    main()
