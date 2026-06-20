from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class RpcRequest:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": self.method,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RpcRequest":
        if data.get("jsonrpc") not in (None, "2.0"):
            raise ProtocolError("Unsupported JSON-RPC version")
        return cls(
            method=str(data.get("method", "")),
            params=dict(data.get("params", {})),
            request_id=str(data.get("id") or uuid.uuid4().hex),
        )


@dataclass(frozen=True)
class RpcResponse:
    ok: bool
    result: Any = None
    error: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "ok": self.ok,
            "id": self.request_id,
        }
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error or "Unknown helper error"
        return payload

    @classmethod
    def success(cls, result: Any = None, request_id: str | None = None) -> "RpcResponse":
        return cls(ok=True, result=result, request_id=request_id)

    @classmethod
    def failure(cls, error: str, request_id: str | None = None) -> "RpcResponse":
        return cls(ok=False, error=error, request_id=request_id)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RpcResponse":
        if data.get("jsonrpc") not in (None, "2.0"):
            raise ProtocolError("Unsupported JSON-RPC version")
        return cls(
            ok=bool(data.get("ok", False)),
            result=data.get("result"),
            error=data.get("error") or None,
            request_id=data.get("id") or None,
        )


def encode_message(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(payload: bytes | str) -> dict[str, Any]:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8")
    else:
        text = payload
    text = text.strip()
    if not text:
        raise ProtocolError("Empty helper message")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ProtocolError("Helper message must be an object")
    return decoded