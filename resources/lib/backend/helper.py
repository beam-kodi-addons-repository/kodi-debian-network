from __future__ import annotations

from .base import NetworkBackend
from ..helper.client import HelperClient
from ..models import AccessPoint, InterfaceKind, NetworkProfile, NetworkSnapshot


def _kind_value(kind: InterfaceKind | str) -> str:
    if isinstance(kind, InterfaceKind):
        return kind.value
    return str(kind)


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
        payload = self._client.call("scan_wifi")
        return tuple(AccessPoint.from_dict(item) for item in payload)

    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        payload = self._client.call("set_interface_enabled", kind=_kind_value(kind), enabled=enabled)
        return NetworkSnapshot.from_dict(payload)

    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        payload = self._client.call("save_profile", profile=profile.to_dict())
        return NetworkProfile.from_dict(payload)

    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        payload = self._client.call("connect_wifi", profile=profile.to_dict())
        return NetworkSnapshot.from_dict(payload)

    def disconnect(self, service_id: str) -> NetworkSnapshot:
        payload = self._client.call("disconnect", service_id=service_id)
        return NetworkSnapshot.from_dict(payload)

    def forget_wifi(self, service_id: str) -> NetworkSnapshot:
        payload = self._client.call("forget_wifi", service_id=service_id)
        return NetworkSnapshot.from_dict(payload)