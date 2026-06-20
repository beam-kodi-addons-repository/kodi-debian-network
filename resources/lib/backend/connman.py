from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from dataclasses import dataclass

from .base import BackendUnavailableError, NetworkBackend
from ..models import AccessPoint, IPv4Configuration, IPv4Mode, InterfaceKind, InterfaceState, NetworkProfile, NetworkSnapshot


SERVICE_LINE_RE = re.compile(r"^(?P<left>.+?)\s{2,}(?P<service_id>(?:wifi|ethernet)_[^\s]+)$")
CONNMAN_ERROR_RE = re.compile(r"\bError\b.*", re.IGNORECASE)


@dataclass(frozen=True)
class TechnologyStatus:
    kind: InterfaceKind
    name: str
    powered: bool
    connected: bool
    mac_address: str | None = None


@dataclass(frozen=True)
class ServiceEntry:
    service_id: str
    name: str
    kind: InterfaceKind
    flags: str
    security: tuple[str, ...]

    @property
    def connected(self) -> bool:
        return "*" in self.flags

    @property
    def autoconnect(self) -> bool:
        return "A" in self.flags

    @property
    def remembered(self) -> bool:
        return self.connected or self.autoconnect


def prefix_length_to_netmask(prefix_length: int) -> str:
    if prefix_length < 0 or prefix_length > 32:
        raise ValueError(f"Invalid prefix length: {prefix_length}")
    network = ipaddress.IPv4Network(f"0.0.0.0/{prefix_length}")
    return str(network.netmask)


def parse_technologies_output(output: str) -> dict[InterfaceKind, TechnologyStatus]:
    technologies: dict[InterfaceKind, TechnologyStatus] = {}
    current_path = ""
    current_values: dict[str, str] = {}

    def commit() -> None:
        nonlocal current_path, current_values
        if not current_path:
            return
        kind_text = current_path.rsplit("/", 1)[-1].strip().lower()
        if kind_text not in (InterfaceKind.WIFI.value, InterfaceKind.ETHERNET.value):
            current_path = ""
            current_values = {}
            return
        kind = InterfaceKind(kind_text)
        technologies[kind] = TechnologyStatus(
            kind=kind,
            name=current_values.get("name", kind.value),
            powered=current_values.get("powered", "false").lower() == "true",
            connected=current_values.get("connected", "false").lower() == "true",
            mac_address=current_values.get("address") or None,
        )
        current_path = ""
        current_values = {}

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("/"):
            commit()
            current_path = line.strip()
            current_values = {}
            continue
        if current_path and "=" in line:
            key, value = [part.strip() for part in line.split("=", 1)]
            current_values[key.lower()] = value

    commit()
    return technologies


def _security_from_service_id(service_id: str) -> tuple[str, ...]:
    if not service_id.startswith("wifi_"):
        return ()
    security = service_id.rsplit("_", 1)[-1]
    if security in {"managed", "none"}:
        return ()
    return (security,)


def _service_label_and_flags(left: str) -> tuple[str, str]:
    if left[:1].isspace():
        return left.strip(), ""
    match = re.match(r"^(?P<flags>[*A-Z]+)\s+(?P<label>.+)$", left)
    if not match:
        return left.strip(), ""
    return match.group("label").strip(), match.group("flags")


def parse_services_output(output: str) -> tuple[ServiceEntry, ...]:
    services: list[ServiceEntry] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match = SERVICE_LINE_RE.match(line)
        if not match:
            continue
        service_id = match.group("service_id")
        kind = InterfaceKind.WIFI if service_id.startswith("wifi_") else InterfaceKind.ETHERNET
        label, flags = _service_label_and_flags(match.group("left"))
        services.append(
            ServiceEntry(
                service_id=service_id,
                name=label,
                kind=kind,
                flags=flags,
                security=_security_from_service_id(service_id),
            )
        )
    return tuple(services)


def extract_connman_error(output: str) -> str | None:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = CONNMAN_ERROR_RE.search(line)
        if match:
            return match.group(0).strip()
    return None


def build_interactive_connect_script(service_id: str, password: str) -> str:
    return "\n".join(("agent on", f"connect {service_id}", password, "quit", ""))


class ConnManBackend(NetworkBackend):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("connmanctl") or "connmanctl"

    @property
    def name(self) -> str:
        return "ConnMan"

    @staticmethod
    def is_tooling_available() -> bool:
        return shutil.which("connmanctl") is not None

    def _run(self, *args: str) -> str:
        if not self.is_tooling_available():
            raise BackendUnavailableError("connmanctl is not installed")

        completed = subprocess.run(
            [self._executable, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            if not message:
                message = f"connmanctl {' '.join(args)} failed with code {completed.returncode}"
            raise BackendUnavailableError(message)
        return completed.stdout

    def _run_interactive(self, script: str) -> str:
        if not self.is_tooling_available():
            raise BackendUnavailableError("connmanctl is not installed")

        completed = subprocess.run(
            [self._executable],
            input=script,
            capture_output=True,
            text=True,
            check=False,
        )
        combined_output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        message = extract_connman_error(combined_output)
        if completed.returncode != 0:
            if not message:
                message = combined_output.strip() or f"Interactive connmanctl failed with code {completed.returncode}"
            raise BackendUnavailableError(message)
        if message:
            raise BackendUnavailableError(message)
        return completed.stdout

    def _technologies(self) -> dict[InterfaceKind, TechnologyStatus]:
        return parse_technologies_output(self._run("technologies"))

    def _services(self) -> tuple[ServiceEntry, ...]:
        return parse_services_output(self._run("services"))

    def _access_points(self) -> tuple[AccessPoint, ...]:
        entries = [entry for entry in self._services() if entry.kind == InterfaceKind.WIFI]
        count = max(len(entries), 1)
        access_points: list[AccessPoint] = []
        for index, entry in enumerate(entries):
            signal = max(10, 100 - int(index * (80 / count)))
            access_points.append(
                AccessPoint(
                    service_id=entry.service_id,
                    ssid=entry.name,
                    signal=signal,
                    security=entry.security,
                    connected=entry.connected,
                    remembered=entry.remembered,
                    autoconnect=entry.autoconnect,
                )
            )
        return tuple(access_points)

    def _apply_profile_config(self, profile: NetworkProfile) -> None:
        self._run("config", profile.service_id, "autoconnect", "on" if profile.autoconnect else "off")

        if profile.ipv4.mode == IPv4Mode.DHCP:
            self._run("config", profile.service_id, "ipv4", "dhcp")
        else:
            if not profile.ipv4.address or profile.ipv4.prefix_length is None or not profile.ipv4.gateway:
                raise BackendUnavailableError("Static IPv4 configuration requires address, prefix length, and gateway")
            self._run(
                "config",
                profile.service_id,
                "ipv4",
                "manual",
                profile.ipv4.address,
                prefix_length_to_netmask(profile.ipv4.prefix_length),
                profile.ipv4.gateway,
            )

        if profile.ipv4.dns_servers:
            self._run("config", profile.service_id, "nameservers", *profile.ipv4.dns_servers)

    def snapshot(self) -> NetworkSnapshot:
        technologies = self._technologies()
        services = self._services()

        wifi = technologies.get(InterfaceKind.WIFI)
        ethernet = technologies.get(InterfaceKind.ETHERNET)
        interfaces = []
        if wifi is not None:
            interfaces.append(
                InterfaceState(
                    name=wifi.name,
                    kind=wifi.kind,
                    enabled=wifi.powered,
                    connected=wifi.connected,
                    ipv4=IPv4Configuration(mode=IPv4Mode.DHCP),
                    mac_address=wifi.mac_address,
                )
            )
        if ethernet is not None:
            interfaces.append(
                InterfaceState(
                    name=ethernet.name,
                    kind=ethernet.kind,
                    enabled=ethernet.powered,
                    connected=ethernet.connected,
                    ipv4=IPv4Configuration(mode=IPv4Mode.DHCP),
                    mac_address=ethernet.mac_address,
                )
            )

        active_service = next((entry.service_id for entry in services if entry.connected), None)
        return NetworkSnapshot(
            wifi_enabled=wifi.powered if wifi is not None else False,
            ethernet_enabled=ethernet.powered if ethernet is not None else False,
            access_points=self._access_points(),
            interfaces=tuple(interfaces),
            active_service_id=active_service,
        )

    def scan_wifi(self) -> tuple[AccessPoint, ...]:
        self._run("scan", "wifi")
        return self._access_points()

    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        interface_kind = kind if isinstance(kind, InterfaceKind) else InterfaceKind(str(kind))
        action = "enable" if enabled else "disable"
        self._run(action, interface_kind.value)
        return self.snapshot()

    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        self._apply_profile_config(profile)
        return profile

    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        try:
            self._run("connect", profile.service_id)
        except BackendUnavailableError as exc:
            if profile.password:
                self._run_interactive(build_interactive_connect_script(profile.service_id, profile.password))
            else:
                raise exc

        self._apply_profile_config(profile)
        return self.snapshot()

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        self._run("disconnect", service_id)
        return self.snapshot()