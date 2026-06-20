from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class IPv4Mode(str, Enum):
    DHCP = "dhcp"
    STATIC = "static"


class InterfaceKind(str, Enum):
    WIFI = "wifi"
    ETHERNET = "ethernet"


def _to_string_tuple(values: Any) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(str(value) for value in values)


def _optional_int(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    return int(value)


@dataclass(frozen=True)
class IPv4Configuration:
    mode: IPv4Mode = IPv4Mode.DHCP
    address: str | None = None
    prefix_length: int | None = None
    gateway: str | None = None
    dns_servers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "address": self.address,
            "prefix_length": self.prefix_length,
            "gateway": self.gateway,
            "dns_servers": list(self.dns_servers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "IPv4Configuration":
        if not data:
            return cls()
        return cls(
            mode=IPv4Mode(str(data.get("mode", IPv4Mode.DHCP.value))),
            address=data.get("address") or None,
            prefix_length=_optional_int(data.get("prefix_length")),
            gateway=data.get("gateway") or None,
            dns_servers=_to_string_tuple(data.get("dns_servers")),
        )


@dataclass(frozen=True)
class AccessPoint:
    service_id: str
    ssid: str
    signal: int = 0
    security: tuple[str, ...] = ()
    connected: bool = False
    remembered: bool = False
    autoconnect: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "ssid": self.ssid,
            "signal": self.signal,
            "security": list(self.security),
            "connected": self.connected,
            "remembered": self.remembered,
            "autoconnect": self.autoconnect,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccessPoint":
        return cls(
            service_id=str(data.get("service_id", "")),
            ssid=str(data.get("ssid", "")),
            signal=int(data.get("signal", 0)),
            security=_to_string_tuple(data.get("security")),
            connected=bool(data.get("connected", False)),
            remembered=bool(data.get("remembered", False)),
            autoconnect=bool(data.get("autoconnect", False)),
        )


@dataclass(frozen=True)
class InterfaceState:
    name: str
    kind: InterfaceKind
    enabled: bool
    connected: bool = False
    ipv4: IPv4Configuration = field(default_factory=IPv4Configuration)
    mac_address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "enabled": self.enabled,
            "connected": self.connected,
            "ipv4": self.ipv4.to_dict(),
            "mac_address": self.mac_address,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InterfaceState":
        return cls(
            name=str(data.get("name", "")),
            kind=InterfaceKind(str(data.get("kind", InterfaceKind.WIFI.value))),
            enabled=bool(data.get("enabled", False)),
            connected=bool(data.get("connected", False)),
            ipv4=IPv4Configuration.from_dict(data.get("ipv4")),
            mac_address=data.get("mac_address") or None,
        )


@dataclass(frozen=True)
class NetworkProfile:
    service_id: str
    ssid: str
    password: str | None = None
    autoconnect: bool = True
    ipv4: IPv4Configuration = field(default_factory=IPv4Configuration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "ssid": self.ssid,
            "password": self.password,
            "autoconnect": self.autoconnect,
            "ipv4": self.ipv4.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NetworkProfile":
        return cls(
            service_id=str(data.get("service_id", "")),
            ssid=str(data.get("ssid", "")),
            password=data.get("password") or None,
            autoconnect=bool(data.get("autoconnect", True)),
            ipv4=IPv4Configuration.from_dict(data.get("ipv4")),
        )


@dataclass(frozen=True)
class NetworkSnapshot:
    wifi_enabled: bool
    ethernet_enabled: bool
    access_points: tuple[AccessPoint, ...] = ()
    interfaces: tuple[InterfaceState, ...] = ()
    active_service_id: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wifi_enabled": self.wifi_enabled,
            "ethernet_enabled": self.ethernet_enabled,
            "access_points": [item.to_dict() for item in self.access_points],
            "interfaces": [item.to_dict() for item in self.interfaces],
            "active_service_id": self.active_service_id,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NetworkSnapshot":
        return cls(
            wifi_enabled=bool(data.get("wifi_enabled", False)),
            ethernet_enabled=bool(data.get("ethernet_enabled", False)),
            access_points=tuple(
                AccessPoint.from_dict(item) for item in data.get("access_points", [])
            ),
            interfaces=tuple(
                InterfaceState.from_dict(item) for item in data.get("interfaces", [])
            ),
            active_service_id=data.get("active_service_id") or None,
            message=data.get("message") or None,
        )