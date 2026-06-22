from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, replace


STATUS_LINE_RE = re.compile(r"^(?P<ip>\S+)\s+(?P<hostname>\S+)\s+(?P<user>\S+)\s+(?P<os>\S+)\s+(?P<status>.+)$")


@dataclass(frozen=True)
class TailscalePeer:
    hostname: str
    ips: tuple[str, ...]
    os: str | None
    online: bool
    is_self: bool = False
    exit_node: bool = False
    exit_node_option: bool = False
    status_text: str | None = None


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


def parse_status_text(output: str) -> dict[str, str]:
    """Maps each peer's Tailscale IP to the free-text status tail of its
    line in plain `tailscale status` output (e.g. "active; offers exit
    node; direct [...]:41641, tx 292 rx 220") -- detail the JSON output
    doesn't expose in a ready-made string.
    """
    status_by_ip: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = STATUS_LINE_RE.match(line)
        if match:
            status_by_ip[match.group("ip")] = match.group("status").strip()
    return status_by_ip


def parse_status_json(output: str, status_by_ip: dict[str, str] | None = None) -> TailscaleStatus:
    data = json.loads(output)

    peers: list[TailscalePeer] = []
    self_data = data.get("Self")
    if self_data:
        peers.append(_peer_from_json(self_data, is_self=True))
    for peer_data in (data.get("Peer") or {}).values():
        peers.append(_peer_from_json(peer_data))
    peers.sort(key=lambda peer: (not peer.is_self, peer.hostname.lower()))

    if status_by_ip:
        peers = [
            replace(peer, status_text=next((status_by_ip[ip] for ip in peer.ips if ip in status_by_ip), None))
            for peer in peers
        ]

    backend_state = data.get("BackendState") or None
    return TailscaleStatus(
        installed=True,
        running=backend_state == "Running",
        backend_state=backend_state,
        peers=tuple(peers),
        health=tuple(str(item) for item in data.get("Health") or ()),
    )


def _status_by_ip(binary: str, timeout: float) -> dict[str, str]:
    # Best-effort enrichment only -- the plain-text command exposes
    # connection detail (direct/relay, exit node, tx/rx) that --json
    # doesn't, but its absence must never break the JSON-derived status.
    try:
        completed = subprocess.run(
            [binary, "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0:
        return {}
    return parse_status_text(completed.stdout)


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
        return parse_status_json(completed.stdout, _status_by_ip(binary, timeout))
    except (ValueError, KeyError) as exc:
        return TailscaleStatus(installed=True, message=str(exc))


@dataclass(frozen=True)
class TailscaleCommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part).strip()


def build_set_enabled_command(enabled: bool, executable: str | None = None) -> list[str]:
    binary = executable or shutil.which("tailscale") or "tailscale"
    return [binary, "up" if enabled else "down"]


def set_enabled(
    enabled: bool,
    executable: str | None = None,
    timeout: float = 15.0,
) -> TailscaleCommandResult:
    # `tailscale up`/`down` write root-owned daemon state, so this must be
    # invoked from the root helper service (see helper/server.py) rather
    # than from the unprivileged Kodi process.
    try:
        completed = subprocess.run(
            build_set_enabled_command(enabled, executable=executable),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return TailscaleCommandResult(returncode=1, stdout="", stderr=str(exc))
    return TailscaleCommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
