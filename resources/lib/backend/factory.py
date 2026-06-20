from __future__ import annotations

from pathlib import Path

from .base import NetworkBackend
from .demo import DemoBackend
from .helper import HelperBackend


def build_backend(mode: str, socket_path: str) -> NetworkBackend:
    normalized_mode = (mode or "auto").strip().lower()

    if normalized_mode == "demo":
        return DemoBackend()

    if normalized_mode == "helper":
        helper = HelperBackend(socket_path=socket_path)
        try:
            helper.ping()
            return helper
        except Exception:
            return DemoBackend()

    if Path(socket_path).exists():
        helper = HelperBackend(socket_path=socket_path)
        try:
            helper.ping()
            return helper
        except Exception:
            return DemoBackend()

    return DemoBackend()