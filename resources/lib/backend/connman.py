from __future__ import annotations

import ipaddress
import os
import pty
import re
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass

from .base import BackendUnavailableError, NetworkBackend
from ..models import AccessPoint, IPv4Configuration, IPv4Mode, InterfaceKind, InterfaceState, NetworkProfile, NetworkSnapshot


SERVICE_LINE_RE = re.compile(r"^(?P<left>.+?)\s{2,}(?P<service_id>(?:wifi|ethernet)_[^\s]+)$")
CONNMAN_ERROR_RE = re.compile(r"\bError\b.*", re.IGNORECASE)
PASSPHRASE_PROMPT_RE = re.compile(r"Passphrase\?", re.IGNORECASE)
RETRY_PROMPT_RE = re.compile(r"Retry\s*\(yes/no\)\?", re.IGNORECASE)
IPV4_LINE_RE = re.compile(r"^IPv4\s*=\s*\[(.*)\]\s*$")
NAMESERVERS_LINE_RE = re.compile(r"^Nameservers\s*=\s*\[(.*)\]\s*$")
IPV4_FIELD_RE = re.compile(r"(\w+)=([^,\]]+)")


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
        # connmanctl's leading flags are Favorite("*"), AutoConnect("A"), and
        # the current state mapped to Ready("R") or Online("O"). "*" only
        # means the service is saved/favorite, not that it's actually
        # associated right now -- that's "R"/"O".
        return "R" in self.flags or "O" in self.flags

    @property
    def autoconnect(self) -> bool:
        return "A" in self.flags

    @property
    def remembered(self) -> bool:
        return "*" in self.flags or self.autoconnect


@dataclass(frozen=True)
class ServiceDetail:
    service_id: str
    ipv4_method: str | None
    address: str | None
    netmask: str | None
    gateway: str | None
    nameservers: tuple[str, ...]

    @property
    def prefix_length(self) -> int | None:
        if not self.netmask:
            return None
        return netmask_to_prefix_length(self.netmask)


def prefix_length_to_netmask(prefix_length: int) -> str:
    if prefix_length < 0 or prefix_length > 32:
        raise ValueError(f"Invalid prefix length: {prefix_length}")
    network = ipaddress.IPv4Network(f"0.0.0.0/{prefix_length}")
    return str(network.netmask)


def netmask_to_prefix_length(netmask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen


def parse_service_detail_output(service_id: str, output: str) -> ServiceDetail:
    ipv4_fields: dict[str, str] = {}
    nameservers: tuple[str, ...] = ()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        ipv4_match = IPV4_LINE_RE.match(line)
        if ipv4_match:
            ipv4_fields = {
                key: value.strip()
                for key, value in IPV4_FIELD_RE.findall(ipv4_match.group(1))
            }
            continue
        ns_match = NAMESERVERS_LINE_RE.match(line)
        if ns_match:
            nameservers = tuple(
                part.strip() for part in ns_match.group(1).split(",") if part.strip()
            )
    return ServiceDetail(
        service_id=service_id,
        ipv4_method=ipv4_fields.get("Method", "").lower() or None,
        address=ipv4_fields.get("Address") or None,
        netmask=ipv4_fields.get("Netmask") or None,
        gateway=ipv4_fields.get("Gateway") or None,
        nameservers=nameservers,
    )


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
    # connmanctl always prints a fixed 3-character flags column (Favorite,
    # AutoConnect, State) followed by a separator space, even when individual
    # flags are absent (rendered as spaces). Slicing by fixed width -- rather
    # than treating a blank first character as "no flags at all" -- is the
    # only way to correctly detect a state flag (R/O) on a non-favorite,
    # non-autoconnect service.
    return left[4:].strip(), left[:3]


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

    @staticmethod
    def _raise_if_connman_error(stdout: str, stderr: str, returncode: int, args: tuple[str, ...]) -> None:
        combined = "\n".join(part for part in (stdout, stderr) if part)
        if returncode != 0:
            message = (stderr or stdout or "").strip()
            if not message:
                message = f"connmanctl {' '.join(args)} failed with code {returncode}"
            raise BackendUnavailableError(message)
        error_message = extract_connman_error(combined)
        if error_message:
            raise BackendUnavailableError(error_message)

    def _run(self, *args: str) -> str:
        if not self.is_tooling_available():
            raise BackendUnavailableError("connmanctl is not installed")

        completed = subprocess.run(
            [self._executable, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self._raise_if_connman_error(completed.stdout, completed.stderr, completed.returncode, args)
        return completed.stdout

    @staticmethod
    def _run_prompt_driver(write_line, read_chunk, is_finished, service_id: str, password: str, timeout: float = 20.0) -> str:
        """Drive connmanctl's interactive agent prompts to completion.

        Decoupled from the actual subprocess/pty plumbing so the prompt
        state machine can be unit tested with fake IO callables.
        """
        output_buffer: list[str] = []

        def read_until(predicate, deadline: float) -> str:
            # Only text read *during this call* is considered, so stale output
            # from a previous step (e.g. connmanctl's benign "Error getting VPN
            # connections" startup banner after "agent on") never gets mistaken
            # for an error/prompt belonging to a later step.
            segment_start = len(output_buffer)
            segment = ""
            while time.monotonic() < deadline:
                if predicate(segment):
                    return segment
                if is_finished():
                    break
                chunk = read_chunk()
                if not chunk:
                    continue
                output_buffer.append(chunk)
                segment = "".join(output_buffer[segment_start:])
            return segment

        write_line("agent on")
        read_until(lambda s: "agent registered" in s.lower(), time.monotonic() + timeout)

        write_line(f"connect {service_id}")
        deadline = time.monotonic() + timeout
        segment = read_until(
            lambda s: PASSPHRASE_PROMPT_RE.search(s) or extract_connman_error(s),
            deadline,
        )

        error = extract_connman_error(segment)
        if error and not PASSPHRASE_PROMPT_RE.search(segment):
            write_line("quit")
            raise BackendUnavailableError(error)

        write_line(password)
        deadline = time.monotonic() + timeout
        segment = read_until(
            lambda s: RETRY_PROMPT_RE.search(s) or "connected" in s.lower() or extract_connman_error(s),
            deadline,
        )

        if RETRY_PROMPT_RE.search(segment):
            write_line("no")
            read_until(lambda s: True, time.monotonic() + 2)
            write_line("quit")
            error_message = extract_connman_error(segment) or "Invalid passphrase"
            raise BackendUnavailableError(error_message)

        error = extract_connman_error(segment)
        if error:
            write_line("quit")
            raise BackendUnavailableError(error)

        write_line("quit")
        return "".join(output_buffer)

    def _drive_interactive_connect(self, service_id: str, password: str, timeout: float = 20.0) -> str:
        if not self.is_tooling_available():
            raise BackendUnavailableError("connmanctl is not installed")

        # connmanctl fully buffers stdout (no line-flush) unless it is
        # connected to a real terminal, so a plain pipe never surfaces its
        # interactive prompts until the process exits. A pty makes connmanctl
        # see a tty and flush its prompts as they're printed.
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [self._executable],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        sel = selectors.DefaultSelector()
        sel.register(master_fd, selectors.EVENT_READ)

        def write_line(line: str) -> None:
            os.write(master_fd, (line + "\n").encode())

        def read_chunk() -> str:
            events = sel.select(timeout=0.5)
            if not events:
                return ""
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                return ""
            return data.decode(errors="replace")

        def is_finished() -> bool:
            return proc.poll() is not None

        try:
            result = self._run_prompt_driver(write_line, read_chunk, is_finished, service_id, password, timeout)
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            return result
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            sel.close()
            os.close(master_fd)

    def _technologies(self) -> dict[InterfaceKind, TechnologyStatus]:
        return parse_technologies_output(self._run("technologies"))

    def _services(self) -> tuple[ServiceEntry, ...]:
        return parse_services_output(self._run("services"))

    def _service_detail(self, service_id: str) -> ServiceDetail:
        output = self._run("services", service_id)
        return parse_service_detail_output(service_id, output)

    def _ipv4_for_kind(self, kind: InterfaceKind, services: tuple[ServiceEntry, ...]) -> IPv4Configuration:
        connected_entry = next(
            (entry for entry in services if entry.kind == kind and entry.connected),
            None,
        )
        if connected_entry is None:
            return IPv4Configuration(mode=IPv4Mode.DHCP)
        try:
            detail = self._service_detail(connected_entry.service_id)
        except BackendUnavailableError:
            return IPv4Configuration(mode=IPv4Mode.DHCP)
        mode = IPv4Mode.STATIC if detail.ipv4_method == "manual" else IPv4Mode.DHCP
        return IPv4Configuration(
            mode=mode,
            address=detail.address,
            prefix_length=detail.prefix_length,
            gateway=detail.gateway,
            dns_servers=detail.nameservers,
        )

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
                    ipv4=self._ipv4_for_kind(InterfaceKind.WIFI, services),
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
                    ipv4=self._ipv4_for_kind(InterfaceKind.ETHERNET, services),
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
                self._drive_interactive_connect(profile.service_id, profile.password)
            else:
                raise exc

        self._apply_profile_config(profile)
        return self.snapshot()

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        self._run("disconnect", service_id)
        return self.snapshot()

    def forget_wifi(self, service_id: str) -> NetworkSnapshot:
        self._run("config", service_id, "--remove")
        return self.snapshot()