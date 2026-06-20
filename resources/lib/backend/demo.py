from __future__ import annotations

from dataclasses import replace

from .base import BackendUnavailableError, NetworkBackend
from ..models import (
    AccessPoint,
    IPv4Configuration,
    IPv4Mode,
    InterfaceKind,
    InterfaceState,
    NetworkProfile,
    NetworkSnapshot,
)


class DemoBackend(NetworkBackend):
    def __init__(self) -> None:
        self._wifi_enabled = True
        self._ethernet_enabled = True
        self._active_service_id = "wifi-home"
        self._profiles: dict[str, NetworkProfile] = {}
        self._access_points: dict[str, AccessPoint] = {
            "wifi-home": AccessPoint(
                service_id="wifi-home",
                ssid="Home Network",
                signal=87,
                security=("wpa2",),
                connected=True,
                remembered=True,
                autoconnect=True,
            ),
            "wifi-guest": AccessPoint(
                service_id="wifi-guest",
                ssid="Guest Network",
                signal=61,
                security=("wpa2",),
                connected=False,
                remembered=False,
                autoconnect=False,
            ),
            "wifi-cafe": AccessPoint(
                service_id="wifi-cafe",
                ssid="Cafe",
                signal=42,
                security=(),
                connected=False,
                remembered=False,
                autoconnect=False,
            ),
        }
        self._interfaces: dict[str, InterfaceState] = {
            "wlan0": InterfaceState(
                name="wlan0",
                kind=InterfaceKind.WIFI,
                enabled=True,
                connected=True,
                ipv4=IPv4Configuration(
                    mode=IPv4Mode.DHCP,
                    address="192.168.1.42",
                    prefix_length=24,
                    gateway="192.168.1.1",
                    dns_servers=("1.1.1.1", "8.8.8.8"),
                ),
                mac_address="02:11:22:33:44:55",
            ),
            "eth0": InterfaceState(
                name="eth0",
                kind=InterfaceKind.ETHERNET,
                enabled=True,
                connected=False,
                ipv4=IPv4Configuration(mode=IPv4Mode.DHCP),
                mac_address="02:aa:bb:cc:dd:ee",
            ),
        }

    @property
    def name(self) -> str:
        return "Demo"

    def _snapshot(self, message: str | None = None) -> NetworkSnapshot:
        return NetworkSnapshot(
            wifi_enabled=self._wifi_enabled,
            ethernet_enabled=self._ethernet_enabled,
            access_points=tuple(
                sorted(self._access_points.values(), key=lambda item: item.signal, reverse=True)
            ),
            interfaces=tuple(self._interfaces[name] for name in sorted(self._interfaces)),
            active_service_id=self._active_service_id,
            message=message,
        )

    def snapshot(self) -> NetworkSnapshot:
        return self._snapshot()

    def scan_wifi(self) -> tuple[AccessPoint, ...]:
        if not self._wifi_enabled:
            return ()
        return tuple(
            sorted(self._access_points.values(), key=lambda item: item.signal, reverse=True)
        )

    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        interface_kind = kind if isinstance(kind, InterfaceKind) else InterfaceKind(str(kind))
        if interface_kind == InterfaceKind.WIFI:
            self._wifi_enabled = enabled
            wlan = self._interfaces["wlan0"]
            self._interfaces["wlan0"] = replace(wlan, enabled=enabled, connected=enabled and wlan.connected)
            if not enabled:
                self._active_service_id = None
                self._interfaces["wlan0"] = replace(self._interfaces["wlan0"], connected=False)
                for service_id, ap in list(self._access_points.items()):
                    self._access_points[service_id] = replace(ap, connected=False)
        else:
            self._ethernet_enabled = enabled
            eth = self._interfaces["eth0"]
            self._interfaces["eth0"] = replace(eth, enabled=enabled, connected=enabled and eth.connected)
            if not enabled:
                self._interfaces["eth0"] = replace(self._interfaces["eth0"], connected=False)
        return self._snapshot()

    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        profile_id = profile.service_id or profile.ssid
        stored_profile = replace(profile, service_id=profile_id)
        self._profiles[profile_id] = stored_profile
        if profile_id in self._access_points:
            ap = self._access_points[profile_id]
            self._access_points[profile_id] = replace(
                ap,
                remembered=True,
                autoconnect=stored_profile.autoconnect,
            )
        return stored_profile

    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        if not self._wifi_enabled:
            raise BackendUnavailableError("Wi-Fi is disabled")

        stored_profile = self.save_profile(profile)
        service_id = stored_profile.service_id
        ap = self._access_points.get(
            service_id,
            AccessPoint(
                service_id=service_id,
                ssid=stored_profile.ssid,
                signal=75,
                security=("wpa2",) if stored_profile.password else (),
            ),
        )

        for access_point_id, access_point in list(self._access_points.items()):
            self._access_points[access_point_id] = replace(access_point, connected=False)

        self._access_points[service_id] = replace(
            ap,
            connected=True,
            remembered=True,
            autoconnect=stored_profile.autoconnect,
        )
        self._active_service_id = service_id

        wlan = self._interfaces["wlan0"]
        self._interfaces["wlan0"] = replace(
            wlan,
            enabled=True,
            connected=True,
            ipv4=stored_profile.ipv4,
        )
        return self._snapshot(message=f"Connected to {stored_profile.ssid}")

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        if service_id in self._access_points:
            self._access_points[service_id] = replace(self._access_points[service_id], connected=False)
        if self._active_service_id == service_id:
            self._active_service_id = None
            wlan = self._interfaces["wlan0"]
            self._interfaces["wlan0"] = replace(wlan, connected=False)
        return self._snapshot(message=f"Disconnected from {service_id}")