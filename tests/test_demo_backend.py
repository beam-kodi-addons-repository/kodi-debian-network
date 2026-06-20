from __future__ import annotations

import unittest

from resources.lib.backend.demo import DemoBackend
from resources.lib.models import IPv4Configuration, IPv4Mode, NetworkProfile


class DemoBackendTests(unittest.TestCase):
    def test_scan_wifi_returns_sorted_networks(self) -> None:
        backend = DemoBackend()
        access_points = backend.scan_wifi()

        self.assertGreaterEqual(len(access_points), 3)
        self.assertEqual(access_points[0].signal, max(item.signal for item in access_points))

    def test_connect_wifi_updates_state(self) -> None:
        backend = DemoBackend()
        profile = NetworkProfile(
            service_id="wifi-lab",
            ssid="Lab",
            password="secret",
            autoconnect=True,
            ipv4=IPv4Configuration(
                mode=IPv4Mode.STATIC,
                address="10.0.0.50",
                prefix_length=24,
                gateway="10.0.0.1",
                dns_servers=("9.9.9.9",),
            ),
        )

        snapshot = backend.connect_wifi(profile)

        self.assertEqual(snapshot.active_service_id, "wifi-lab")
        self.assertTrue(snapshot.wifi_enabled)
        self.assertTrue(any(item.service_id == "wifi-lab" and item.connected for item in snapshot.access_points))


if __name__ == "__main__":
    unittest.main()