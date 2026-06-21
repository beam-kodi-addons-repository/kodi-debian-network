from __future__ import annotations

import socket
from typing import Any, Mapping

from ..protocol import RpcRequest, RpcResponse, decode_message, encode_message


class HelperError(RuntimeError):
    pass


class HelperUnavailableError(HelperError):
    pass


class HelperClient:
    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, method: str, timeout: float | None = None, **params: Any) -> Any:
        request = RpcRequest(method=method, params=params)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout if timeout is not None else self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(encode_message(request.to_dict()))
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("rb") as stream:
                    response_line = stream.readline()
        except OSError as exc:
            raise HelperUnavailableError(f"Helper socket is unavailable: {self.socket_path}") from exc

        response = RpcResponse.from_dict(decode_message(response_line))
        if not response.ok:
            raise HelperError(response.error or "Helper call failed")
        return response.result

    def ping(self) -> Mapping[str, Any]:
        result = self.call("ping")
        if not isinstance(result, Mapping):
            raise HelperError("Helper ping returned an invalid payload")
        return result