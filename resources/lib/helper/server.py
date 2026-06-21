from __future__ import annotations

import argparse
import socketserver
import sys
from pathlib import Path
from typing import Any, Mapping


ADDON_ROOT = Path(__file__).resolve().parents[3]
if str(ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDON_ROOT))

from resources.lib.backend.base import BackendUnavailableError
from resources.lib.backend.demo import DemoBackend
from resources.lib.backend.networkmanager import NetworkManagerBackend
from resources.lib.models import NetworkProfile
from resources.lib.protocol import RpcRequest, RpcResponse, decode_message, encode_message


class HelperUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class HelperRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request_line = self.rfile.readline()
        if not request_line:
            return

        try:
            payload = decode_message(request_line)
            response = dispatch_request(self.server.backend, payload)
        except Exception as exc:  # pragma: no cover - helper boundary
            response = RpcResponse.failure(str(exc)).to_dict()

        self.wfile.write(encode_message(response))


def dispatch_request(backend: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    request = RpcRequest.from_dict(payload)

    if request.method == "ping":
        return RpcResponse.success({"status": "ok", "backend": backend.name}, request.request_id).to_dict()

    if request.method == "snapshot":
        return RpcResponse.success(backend.snapshot().to_dict(), request.request_id).to_dict()

    if request.method == "scan_wifi":
        return RpcResponse.success(
            [access_point.to_dict() for access_point in backend.scan_wifi()],
            request.request_id,
        ).to_dict()

    if request.method == "set_interface_enabled":
        snapshot = backend.set_interface_enabled(
            request.params.get("kind", "wifi"),
            bool(request.params.get("enabled", False)),
        )
        return RpcResponse.success(snapshot.to_dict(), request.request_id).to_dict()

    if request.method == "save_profile":
        profile_payload = request.params.get("profile", request.params)
        profile = NetworkProfile.from_dict(profile_payload)
        return RpcResponse.success(backend.save_profile(profile).to_dict(), request.request_id).to_dict()

    if request.method == "connect_wifi":
        profile_payload = request.params.get("profile", request.params)
        profile = NetworkProfile.from_dict(profile_payload)
        return RpcResponse.success(backend.connect_wifi(profile).to_dict(), request.request_id).to_dict()

    if request.method == "disconnect":
        snapshot = backend.disconnect(str(request.params.get("service_id", "")))
        return RpcResponse.success(snapshot.to_dict(), request.request_id).to_dict()

    if request.method == "forget_wifi":
        snapshot = backend.forget_wifi(str(request.params.get("service_id", "")))
        return RpcResponse.success(snapshot.to_dict(), request.request_id).to_dict()

    raise BackendUnavailableError(f"Unsupported helper method: {request.method}")


def create_backend(mode: str):
    normalized_mode = (mode or "auto").strip().lower()
    if normalized_mode == "networkmanager":
        return NetworkManagerBackend()
    if normalized_mode == "auto" and NetworkManagerBackend.is_tooling_available():
        return NetworkManagerBackend()
    return DemoBackend()


def run_server(socket_path: str, backend_mode: str = "auto") -> None:
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.unlink(missing_ok=True)

    backend = create_backend(backend_mode)
    server = HelperUnixServer(str(socket_file), HelperRequestHandler)
    server.backend = backend

    try:
        server.serve_forever()
    finally:
        server.server_close()
        socket_file.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Network Assistant helper service")
    parser.add_argument("--socket", default="/run/kodi-network-helper.sock")
    parser.add_argument("--backend", default="auto", choices=("auto", "demo", "networkmanager"))
    args = parser.parse_args(argv)
    run_server(args.socket, args.backend)


if __name__ == "__main__":
    main()