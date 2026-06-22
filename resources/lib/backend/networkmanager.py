from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from typing import Iterable

from .base import BackendUnavailableError, NetworkBackend
from ..models import AccessPoint, IPv4Configuration, IPv4Mode, InterfaceKind, InterfaceState, NetworkProfile, NetworkSnapshot


UNESCAPED_COLON_RE = re.compile(r"(?<!\\):")
BAND_ORDER = ("2.4GHz", "5GHz", "6GHz")


def unescape_value(value: str) -> str:
    """Undo nmcli's backslash-escaping of ':' and '\\' inside a field value.

    nmcli escapes literal ':' (and '\\') in *every* machine-readable output
    mode -- not just `-t -f` rows with several fields on one line, but also
    single-field `-g` reads like `-g GENERAL.HWADDR device show <iface>`,
    since the same value could in principle be combined with other fields.
    """
    return value.replace("\\:", ":").replace("\\\\", "\\")


def split_terse_fields(line: str) -> tuple[str, ...]:
    """Split one row of `nmcli -t -f ...` output.

    nmcli's terse mode separates fields with ':' but backslash-escapes any
    literal ':' or '\\' inside a field value (e.g. a BSSID or an IPv4/prefix
    pair), since those would otherwise be indistinguishable from the field
    separator.
    """
    raw_parts = UNESCAPED_COLON_RE.split(line)
    return tuple(unescape_value(part) for part in raw_parts)


def band_from_frequency(freq_mhz: int) -> str:
    if freq_mhz < 3000:
        return "2.4GHz"
    if freq_mhz < 5925:
        return "5GHz"
    return "6GHz"


def combine_bands(bands: Iterable[str | None]) -> str | None:
    unique = {band for band in bands if band}
    if not unique:
        return None
    ordered = [band for band in BAND_ORDER if band in unique]
    ordered += sorted(unique - set(ordered))
    return "/".join(ordered)


@dataclass(frozen=True)
class DeviceStatus:
    device: str
    kind: InterfaceKind
    state: str
    connection: str | None


@dataclass(frozen=True)
class ConnectionProfile:
    name: str
    uuid: str
    kind: InterfaceKind
    autoconnect: bool
    active: bool
    ssid: str | None


@dataclass(frozen=True)
class WifiScanEntry:
    in_use: bool
    ssid: str
    bssid: str
    security_label: str | None
    signal: int
    band: str | None = None


def parse_device_status_output(output: str) -> tuple[DeviceStatus, ...]:
    statuses: list[DeviceStatus] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        fields = split_terse_fields(raw_line)
        if len(fields) < 4:
            continue
        device, kind_text, state, connection = fields[:4]
        kind_text = kind_text.strip().lower()
        if kind_text not in (InterfaceKind.WIFI.value, InterfaceKind.ETHERNET.value):
            continue
        connection_value = connection.strip()
        statuses.append(
            DeviceStatus(
                device=device.strip(),
                kind=InterfaceKind(kind_text),
                state=state.strip().lower(),
                connection=connection_value if connection_value not in ("", "--") else None,
            )
        )
    return tuple(statuses)


def parse_connections_output(output: str) -> tuple[ConnectionProfile, ...]:
    # `connection show` (the list form) only allows the generic connection
    # fields (NAME, UUID, TYPE, AUTOCONNECT, ACTIVE, ...) -- type-specific
    # properties like 802-11-wireless.ssid are only valid for `connection
    # show <id>` against a single connection, so the SSID is resolved
    # separately per wifi connection (see NetworkManagerBackend._connections).
    connections: list[ConnectionProfile] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        fields = split_terse_fields(raw_line)
        if len(fields) < 5:
            continue
        name, uuid, kind_text, autoconnect, active = fields[:5]
        kind_text = kind_text.strip()
        if kind_text == "802-11-wireless":
            kind = InterfaceKind.WIFI
        elif kind_text == "802-3-ethernet":
            kind = InterfaceKind.ETHERNET
        else:
            continue
        connections.append(
            ConnectionProfile(
                name=name.strip(),
                uuid=uuid.strip(),
                kind=kind,
                autoconnect=autoconnect.strip().lower() == "yes",
                active=active.strip().lower() == "yes",
                ssid=None,
            )
        )
    return tuple(connections)


def parse_wifi_list_output(output: str) -> tuple[WifiScanEntry, ...]:
    entries: list[WifiScanEntry] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        fields = split_terse_fields(raw_line)
        if len(fields) < 6:
            continue
        in_use, ssid, bssid, security, signal, freq = fields[:6]
        security_text = security.strip()
        security_label = security_text if security_text not in ("", "--") else None
        try:
            signal_value = int(signal.strip())
        except ValueError:
            signal_value = 0
        freq_text = freq.strip().split()[0] if freq.strip() else ""
        band = band_from_frequency(int(freq_text)) if freq_text.isdigit() else None
        entries.append(
            WifiScanEntry(
                in_use=in_use.strip() == "*",
                ssid=ssid.strip(),
                bssid=bssid.strip().lower(),
                security_label=security_label,
                signal=signal_value,
                band=band,
            )
        )
    return tuple(entries)


def parse_ip4_address_block(output: str) -> tuple[str | None, int | None, str | None, tuple[str, ...]]:
    """Parse a 3-line `-g ...address-field,...gateway-field,...dns-field` read.

    Used for both `ipv4.addresses,ipv4.gateway,ipv4.dns connection show <id>`
    (a saved static profile) and `IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show
    <iface>` (the live, currently-assigned configuration) -- both lay out as
    one requested field's value per line, blank when a field has no value.
    """
    lines = [unescape_value(line.strip()) for line in output.splitlines()]
    addresses = lines[0] if len(lines) > 0 else ""
    gateway = lines[1] if len(lines) > 1 else ""
    dns = lines[2] if len(lines) > 2 else ""

    address: str | None = None
    prefix_length: int | None = None
    if addresses:
        first = addresses.split(",")[0].strip()
        if "/" in first:
            address, prefix_text = first.split("/", 1)
            address = address.strip() or None
            try:
                prefix_length = int(prefix_text.strip())
            except ValueError:
                prefix_length = None
        else:
            address = first or None

    dns_servers = tuple(part.strip() for part in dns.split(",") if part.strip())
    return address, prefix_length, gateway or None, dns_servers


class NetworkManagerBackend(NetworkBackend):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("nmcli") or "nmcli"

    @property
    def name(self) -> str:
        return "NetworkManager"

    @staticmethod
    def is_tooling_available() -> bool:
        return shutil.which("nmcli") is not None

    def _run(self, *args: str) -> str:
        if not self.is_tooling_available():
            raise BackendUnavailableError("nmcli is not installed")

        completed = subprocess.run(
            [self._executable, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            if not message:
                message = f"nmcli {' '.join(args)} failed with code {completed.returncode}"
            raise BackendUnavailableError(message)
        return completed.stdout

    def _run_terse(self, fields: str, *args: str) -> str:
        return self._run("-t", "-f", fields, *args)

    def _device_statuses(self) -> tuple[DeviceStatus, ...]:
        output = self._run_terse("DEVICE,TYPE,STATE,CONNECTION", "device", "status")
        return parse_device_status_output(output)

    def _connections(self) -> tuple[ConnectionProfile, ...]:
        output = self._run_terse("NAME,UUID,TYPE,AUTOCONNECT,ACTIVE", "connection", "show")
        connections = parse_connections_output(output)
        return tuple(
            replace(connection, ssid=self._connection_ssid(connection.uuid))
            if connection.kind is InterfaceKind.WIFI
            else connection
            for connection in connections
        )

    def _connection_ssid(self, uuid: str) -> str | None:
        try:
            output = self._run("-g", "802-11-wireless.ssid", "connection", "show", uuid)
        except BackendUnavailableError:
            return None
        return output.strip() or None

    def _wifi_radio_enabled(self) -> bool:
        try:
            output = self._run("radio", "wifi")
        except BackendUnavailableError:
            return False
        return output.strip().lower() == "enabled"

    def _hwaddr(self, iface: str) -> str | None:
        try:
            output = self._run("-g", "GENERAL.HWADDR", "device", "show", iface)
        except BackendUnavailableError:
            return None
        return unescape_value(output.strip()) or None

    def _ipv4_for_interface(self, iface: str, uuid: str | None, connected: bool) -> IPv4Configuration:
        if not uuid:
            return IPv4Configuration(mode=IPv4Mode.DHCP)
        try:
            method_output = self._run("-g", "ipv4.method", "connection", "show", uuid)
        except BackendUnavailableError:
            return IPv4Configuration(mode=IPv4Mode.DHCP)
        method = unescape_value(method_output.strip())
        mode = IPv4Mode.STATIC if method == "manual" else IPv4Mode.DHCP

        if connected:
            # The connection profile only stores the *configured* address,
            # which is blank for DHCP ("auto") profiles -- the actually
            # assigned address lives on the device instead, so read it live
            # there whenever the interface is up.
            try:
                output = self._run("-g", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", iface)
            except BackendUnavailableError:
                return IPv4Configuration(mode=mode)
        elif mode is IPv4Mode.STATIC:
            try:
                output = self._run("-g", "ipv4.addresses,ipv4.gateway,ipv4.dns", "connection", "show", uuid)
            except BackendUnavailableError:
                return IPv4Configuration(mode=mode)
        else:
            return IPv4Configuration(mode=mode)

        address, prefix_length, gateway, dns_servers = parse_ip4_address_block(output)
        return IPv4Configuration(
            mode=mode,
            address=address,
            prefix_length=prefix_length,
            gateway=gateway,
            dns_servers=dns_servers,
        )

    def _wifi_iface(self, statuses: tuple[DeviceStatus, ...]) -> str | None:
        return next((status.device for status in statuses if status.kind is InterfaceKind.WIFI), None)

    def _wifi_list(self, rescan: bool) -> tuple[WifiScanEntry, ...]:
        iface = self._wifi_iface(self._device_statuses())
        if not iface:
            return ()
        try:
            output = self._run_terse(
                "IN-USE,SSID,BSSID,SECURITY,SIGNAL,FREQ",
                "device",
                "wifi",
                "list",
                "ifname",
                iface,
                "--rescan",
                "yes" if rescan else "no",
            )
        except BackendUnavailableError:
            return ()
        return parse_wifi_list_output(output)

    def _access_points(self, rescan: bool = False) -> tuple[AccessPoint, ...]:
        entries = self._wifi_list(rescan=rescan)
        connections_by_ssid = {
            connection.ssid: connection
            for connection in self._connections()
            if connection.kind is InterfaceKind.WIFI and connection.ssid
        }

        visible: dict[str, list[WifiScanEntry]] = {}
        hidden: list[WifiScanEntry] = []
        for entry in entries:
            if entry.ssid:
                visible.setdefault(entry.ssid, []).append(entry)
            else:
                hidden.append(entry)

        access_points: list[AccessPoint] = []
        for ssid, matches in visible.items():
            connection = connections_by_ssid.get(ssid)
            # A dual-band AP broadcasts the same SSID as two separate BSSIDs
            # (one per radio) -- merge their bands rather than showing only
            # whichever scan row happened to come first.
            primary = next((match for match in matches if match.in_use), matches[0])
            band = combine_bands(match.band for match in matches)
            access_points.append(
                AccessPoint(
                    service_id=connection.uuid if connection else f"wifi:{primary.bssid}",
                    ssid=ssid,
                    signal=primary.signal,
                    security=(primary.security_label,) if primary.security_label else (),
                    connected=any(match.in_use for match in matches),
                    remembered=connection is not None,
                    autoconnect=connection.autoconnect if connection else False,
                    security_label=primary.security_label,
                    bssid=primary.bssid,
                    band=band,
                )
            )

        for entry in hidden:
            # Nearby hidden-SSID BSSIDs are usually unrelated physical APs,
            # not one network -- list each as its own candidate row.
            access_points.append(
                AccessPoint(
                    service_id=f"wifi:{entry.bssid}",
                    ssid="",
                    signal=entry.signal,
                    security=(entry.security_label,) if entry.security_label else (),
                    connected=entry.in_use,
                    remembered=False,
                    autoconnect=False,
                    security_label=entry.security_label,
                    bssid=entry.bssid,
                    band=entry.band,
                )
            )

        return tuple(access_points)

    def _find_connection_uuid_by_ssid(self, ssid: str | None) -> str | None:
        if not ssid:
            return None
        return next(
            (connection.uuid for connection in self._connections() if connection.ssid == ssid),
            None,
        )

    def _apply_profile_config(self, uuid: str, profile: NetworkProfile) -> None:
        args = ["connection", "modify", uuid, "connection.autoconnect", "yes" if profile.autoconnect else "no"]

        if profile.ipv4.mode == IPv4Mode.DHCP:
            args += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""]
        else:
            if not profile.ipv4.address or profile.ipv4.prefix_length is None or not profile.ipv4.gateway:
                raise BackendUnavailableError("Static IPv4 configuration requires address, prefix length, and gateway")
            args += [
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                f"{profile.ipv4.address}/{profile.ipv4.prefix_length}",
                "ipv4.gateway",
                profile.ipv4.gateway,
            ]

        args += ["ipv4.dns", ",".join(profile.ipv4.dns_servers) if profile.ipv4.dns_servers else ""]

        self._run(*args)
        self._run("connection", "up", uuid)

    def snapshot(self) -> NetworkSnapshot:
        statuses = self._device_statuses()
        connections = self._connections()

        wifi_status = next((status for status in statuses if status.kind is InterfaceKind.WIFI), None)
        ethernet_status = next((status for status in statuses if status.kind is InterfaceKind.ETHERNET), None)
        wifi_powered = self._wifi_radio_enabled()

        interfaces: list[InterfaceState] = []
        active_service_id: str | None = None

        if wifi_status is not None:
            connection = next(
                (c for c in connections if c.kind is InterfaceKind.WIFI and c.name == wifi_status.connection),
                None,
            )
            wifi_connected = wifi_status.state == "connected"
            interfaces.append(
                InterfaceState(
                    name=wifi_status.device,
                    kind=InterfaceKind.WIFI,
                    enabled=wifi_powered,
                    connected=wifi_connected,
                    ipv4=self._ipv4_for_interface(
                        wifi_status.device, connection.uuid if connection else None, wifi_connected
                    ),
                    mac_address=self._hwaddr(wifi_status.device),
                )
            )
            if connection is not None:
                active_service_id = connection.uuid

        ethernet_enabled = ethernet_status is not None and ethernet_status.state != "unmanaged"
        if ethernet_status is not None:
            connection = next(
                (c for c in connections if c.kind is InterfaceKind.ETHERNET and c.name == ethernet_status.connection),
                None,
            )
            ethernet_connected = ethernet_status.state == "connected"
            interfaces.append(
                InterfaceState(
                    name=ethernet_status.device,
                    kind=InterfaceKind.ETHERNET,
                    enabled=ethernet_enabled,
                    connected=ethernet_connected,
                    ipv4=self._ipv4_for_interface(
                        ethernet_status.device, connection.uuid if connection else None, ethernet_connected
                    ),
                    mac_address=self._hwaddr(ethernet_status.device),
                )
            )
            if active_service_id is None and connection is not None:
                active_service_id = connection.uuid

        return NetworkSnapshot(
            wifi_enabled=wifi_powered,
            ethernet_enabled=ethernet_enabled,
            access_points=self._access_points(),
            interfaces=tuple(interfaces),
            active_service_id=active_service_id,
        )

    def scan_wifi(self) -> tuple[AccessPoint, ...]:
        return self._access_points(rescan=True)

    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        interface_kind = kind if isinstance(kind, InterfaceKind) else InterfaceKind(str(kind))
        if interface_kind is InterfaceKind.WIFI:
            self._run("radio", "wifi", "on" if enabled else "off")
        else:
            # NetworkManager has no global ethernet radio kill switch.
            # "device disconnect" only drops the active connection -- the
            # device state becomes "disconnected", never "unmanaged", so
            # snapshot()'s `state != "unmanaged"` check kept reporting the
            # interface as enabled even though it had no networking.
            # Un-managing the device is what actually flips that state.
            for status in self._device_statuses():
                if status.kind is InterfaceKind.ETHERNET:
                    self._run("device", "set", status.device, "managed", "yes" if enabled else "no")
                    if enabled:
                        self._run("device", "connect", status.device)
        return self.snapshot()

    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        if not profile.service_id.startswith("wifi:"):
            self._apply_profile_config(profile.service_id, profile)
        return profile

    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        connection_id = profile.service_id

        if connection_id.startswith("wifi:"):
            bssid = connection_id.split(":", 1)[1]
            target = profile.ssid or bssid
            args = ["device", "wifi", "connect", target]
            if profile.ssid:
                args += ["hidden", "yes"]
            if profile.password:
                args += ["password", profile.password]
            iface = self._wifi_iface(self._device_statuses())
            if iface:
                args += ["ifname", iface]
            self._run(*args)
            connection_id = self._find_connection_uuid_by_ssid(profile.ssid)
        else:
            self._run("connection", "up", connection_id)

        if connection_id and not connection_id.startswith("wifi:"):
            self._apply_profile_config(connection_id, profile)

        return self.snapshot()

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        if not service_id.startswith("wifi:"):
            self._run("connection", "down", service_id)
        return self.snapshot()

    def forget_wifi(self, service_id: str) -> NetworkSnapshot:
        if not service_id.startswith("wifi:"):
            self._run("connection", "delete", service_id)
        return self.snapshot()
