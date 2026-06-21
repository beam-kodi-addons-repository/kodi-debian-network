from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TailscalePeer:
    hostname: str
    ips: tuple[str, ...]
    os: str | None
    online: bool
    is_self: bool = False
    exit_node: bool = False
    exit_node_option: bool = False


@dataclass(frozen=True)
class TailscaleStatus:
    installed: bool
    running: bool = False
    backend_state: str | None = None
    peers: tuple[TailscalePeer, ...] = ()
    health: tuple[str, ...] = ()
    message: str | None = None


def is_installed(executable: str | None = None) -> bool:
    return bool(executable or shutil.which("tailscale"))


def _peer_from_json(data: dict, is_self: bool = False) -> TailscalePeer:
    return TailscalePeer(
        hostname=str(data.get("HostName") or data.get("DNSName") or ""),
        ips=tuple(str(ip) for ip in data.get("TailscaleIPs") or ()),
        os=data.get("OS") or None,
        online=bool(data.get("Online", is_self)),
        is_self=is_self,
        exit_node=bool(data.get("ExitNode", False)),
        exit_node_option=bool(data.get("ExitNodeOption", False)),
    )


def parse_status_json(output: str) -> TailscaleStatus:
    data = json.loads(output)

    peers: list[TailscalePeer] = []
    self_data = data.get("Self")
    if self_data:
        peers.append(_peer_from_json(self_data, is_self=True))
    for peer_data in (data.get("Peer") or {}).values():
        peers.append(_peer_from_json(peer_data))
    peers.sort(key=lambda peer: (not peer.is_self, peer.hostname.lower()))

    backend_state = data.get("BackendState") or None
    return TailscaleStatus(
        installed=True,
        running=backend_state == "Running",
        backend_state=backend_state,
        peers=tuple(peers),
        health=tuple(str(item) for item in data.get("Health") or ()),
    )


def get_status(executable: str | None = None, timeout: float = 5.0) -> TailscaleStatus:
    binary = executable or shutil.which("tailscale")
    if not binary:
        return TailscaleStatus(installed=False, message="Tailscale is not installed")

    try:
        completed = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return TailscaleStatus(installed=True, message=str(exc))

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return TailscaleStatus(installed=True, message=message or "tailscale status failed")

    try:
        return parse_status_json(completed.stdout)
    except (ValueError, KeyError) as exc:
        return TailscaleStatus(installed=True, message=str(exc))
