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