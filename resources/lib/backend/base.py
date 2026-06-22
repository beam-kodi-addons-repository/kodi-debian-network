from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccessPoint, InterfaceKind, NetworkProfile, NetworkSnapshot


class BackendUnavailableError(RuntimeError):
    pass


class NetworkBackend(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> NetworkSnapshot:
        raise NotImplementedError

    @abstractmethod
    def scan_wifi(self) -> tuple[AccessPoint, ...]:
        raise NotImplementedError

    @abstractmethod
    def set_interface_enabled(self, kind: InterfaceKind | str, enabled: bool) -> NetworkSnapshot:
        raise NotImplementedError

    @abstractmethod
    def save_profile(self, profile: NetworkProfile) -> NetworkProfile:
        raise NotImplementedError

    @abstractmethod
    def connect_wifi(self, profile: NetworkProfile) -> NetworkSnapshot:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self, service_id: str) -> NetworkSnapshot:
        raise NotImplementedError

    @abstractmethod
    def forget_wifi(self, service_id: str) -> NetworkSnapshot:
        raise NotImplementedError

    def set_tailscale_enabled(self, enabled: bool) -> dict[str, object]:
        # Tailscale up/down write root-owned daemon state, so only the
        # helper backend (talking to the root helper service) can do
        # this -- the demo backend has no privileged process to ask.
        raise BackendUnavailableError("Tailscale control requires the helper service")