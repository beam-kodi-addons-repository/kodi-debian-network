from __future__ import annotations

import unittest

from resources.lib.models import (
    AccessPoint,
    IPv4Configuration,
    IPv4Mode,
    InterfaceKind,
    InterfaceState,
    NetworkProfile,
    NetworkSnapshot,
)


class ModelRoundTripTests(unittest.TestCase):
    def test_snapshot_round_trip(self) -> None:
        snapshot = NetworkSnapshot(
            wifi_enabled=True,
            ethernet_enabled=False,
            access_points=(
                AccessPoint(
                    service_id="wifi-home",
                    ssid="Home Network",
                    signal=87,
                    security=("wpa2",),
                    connected=True,
                    remembered=True,
                    autoconnect=True,
                ),
            ),
            interfaces=(
                InterfaceState(
                    name="wlan0",
                    kind=InterfaceKind.WIFI,
                    enabled=True,
                    connected=True,
                    ipv4=IPv4Configuration(
                        mode=IPv4Mode.STATIC,
                        address="192.168.1.50",
                        prefix_length=24,
                        gateway="192.168.1.1",
                        dns_servers=("1.1.1.1", "8.8.8.8"),
                    ),
                ),
            ),
            active_service_id="wifi-home",
        )

        self.assertEqual(NetworkSnapshot.from_dict(snapshot.to_dict()), snapshot)

    def test_profile_round_trip(self) -> None:
        profile = NetworkProfile(
            service_id="wifi-guest",
            ssid="Guest Network",
            password="secret",
            autoconnect=False,
            ipv4=IPv4Configuration(mode=IPv4Mode.DHCP),
        )

        self.assertEqual(NetworkProfile.from_dict(profile.to_dict()), profile)


if __name__ == "__main__":
    unittest.main()