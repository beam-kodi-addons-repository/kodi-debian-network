from __future__ import annotations

from .base import NetworkBackend
from ..helper.client import HelperClient
from ..models import AccessPoint, InterfaceKind, NetworkProfile, NetworkSnapshot


def _kind_value(kind: InterfaceKind | str) -> str:
    if isinstance(kind, InterfaceKind):
        return kind.value
    return str(kind)


# nmcli's `connection up`/`device wifi connect` can legitimately take many
# seconds (association, DHCP lease, an active rescan) -- on a real device
# `nmcli connection up` alone has been observed to take 8+ seconds even for
# an already-known network. The default socket timeout only needs to cover
# quick status calls; mutating calls get a much longer budget so the client
# doesn't give up (and report "helper socket is unavailable") while the
# helper is still working and about to succeed.
SCAN_TIMEOUT = 20.0
CONNECT_TIMEOUT = 30.0
MUTATE_TIMEOUT = 15.0


class HelperBackend(NetworkBackend):
    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        self._client = HelperClient(socket_path=socket_path, timeout=timeout)

    @property
    def name(self) -> str:
        return "Helper"

    def ping(self) -> dict[str, object]:
        return dict(self._client.ping())

    def snapshot(self) -> NetworkSnapshot:
        payload = self._client.call("snapshot")
        return NetworkSnapshot.from_dict(payload)

    def scan_wifi(self) -> tuple[AccessPoint, ...]:
        payload = self._client.call("scan_wifi", timeout=SCAN_TIMEOUT)
        return tuple(AccessPoint.from_dict(item) for item in payload)

    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        payload = self._client.call(
            "set_interface_enabled", timeout=MUTATE_TIMEOUT, kind=_kind_value(kind), enabled=enabled
        )
        return NetworkSnapshot.from_dict(payload)

    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        payload = self._client.call("save_profile", timeout=MUTATE_TIMEOUT, profile=profile.to_dict())
        return NetworkProfile.from_dict(payload)

    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        payload = self._client.call("connect_wifi", timeout=CONNECT_TIMEOUT, profile=profile.to_dict())
        return NetworkSnapshot.from_dict(payload)

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        payload = self._client.call("disconnect", timeout=MUTATE_TIMEOUT, service_id=service_id)
        return NetworkSnapshot.from_dict(payload)

    def forget_wifi(self, service_id: str) -> NetworkSnapshot:
        payload = self._client.call("forget_wifi", timeout=MUTATE_TIMEOUT, service_id=service_id)
        return NetworkSnapshot.from_dict(payload)

    def set_tailscale_enabled(self, enabled: bool) -> dict[str, object]:
        return dict(self._client.call("set_tailscale_enabled", timeout=MUTATE_TIMEOUT, enabled=enabled))