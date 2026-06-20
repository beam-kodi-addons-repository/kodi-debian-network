from __future__ import annotations

import unittest

from resources.lib.protocol import RpcRequest, RpcResponse, decode_message, encode_message


class ProtocolTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        request = RpcRequest(method="snapshot", params={"scope": "all"})
        encoded = encode_message(request.to_dict())
        decoded = decode_message(encoded)
        restored = RpcRequest.from_dict(decoded)

        self.assertEqual(restored.method, request.method)
        self.assertEqual(restored.params, request.params)

    def test_response_round_trip(self) -> None:
        response = RpcResponse.success({"status": "ok"}, request_id="abc123")
        encoded = encode_message(response.to_dict())
        decoded = decode_message(encoded)
        restored = RpcResponse.from_dict(decoded)

        self.assertTrue(restored.ok)
        self.assertEqual(restored.result, {"status": "ok"})
        self.assertEqual(restored.request_id, "abc123")


if __name__ == "__main__":
    unittest.main()