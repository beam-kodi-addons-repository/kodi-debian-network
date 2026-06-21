from __future__ import annotations

import tempfile
import threading
import unittest

from resources.lib.helper.client import HelperClient
from resources.lib.helper.server import HelperRequestHandler, HelperUnixServer
from resources.lib.backend.demo import DemoBackend
from resources.lib.models import IPv4Configuration, IPv4Mode, NetworkProfile


class HelperServerTests(unittest.TestCase):
    def test_round_trip_over_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = f"{temp_dir}/helper.sock"
            server = HelperUnixServer(socket_path, HelperRequestHandler)
            server.backend = DemoBackend()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)

            client = HelperClient(socket_path=socket_path, timeout=2.0)
            ping = client.ping()
            self.assertEqual(ping["status"], "ok")

            snapshot = client.call("snapshot")
            self.assertTrue(snapshot["wifi_enabled"])

            profile = NetworkProfile(
                service_id="wifi-test",
                ssid="Test",
                password="secret",
                autoconnect=False,
                ipv4=IPv4Configuration(
                    mode=IPv4Mode.STATIC,
                    address="10.10.10.10",
                    prefix_length=24,
                    gateway="10.10.10.1",
                    dns_servers=("1.1.1.1",),
                ),
            )
            result = client.call("connect_wifi", profile=profile.to_dict())
            self.assertEqual(result["active_service_id"], "wifi-test")

            disconnected = client.call("disconnect", service_id="wifi-test")
            self.assertIsNone(disconnected["active_service_id"])

            forgotten = client.call("forget_wifi", service_id="wifi-test")
            forgotten_ap = next(ap for ap in forgotten["access_points"] if ap["service_id"] == "wifi-test")
            self.assertFalse(forgotten_ap["remembered"])


if __name__ == "__main__":
    unittest.main()