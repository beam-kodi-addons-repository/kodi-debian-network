from __future__ import annotations

import unittest

from resources.lib.app import NetworkAssistantApp
from resources.lib.models import AccessPoint


class AppLogicTests(unittest.TestCase):
    def test_password_is_required_without_known_access_point(self) -> None:
        self.assertTrue(NetworkAssistantApp._needs_password(None))

    def test_password_is_not_required_for_remembered_secured_network(self) -> None:
        access_point = AccessPoint(
            service_id="wifi-home",
            ssid="Home Network",
            signal=90,
            security=("psk",),
            remembered=True,
        )
        self.assertFalse(NetworkAssistantApp._needs_password(access_point))

    def test_password_is_required_for_new_secured_network(self) -> None:
        access_point = AccessPoint(
            service_id="wifi-new",
            ssid="New Network",
            signal=70,
            security=("psk",),
            remembered=False,
        )
        self.assertTrue(NetworkAssistantApp._needs_password(access_point))


if __name__ == "__main__":
    unittest.main()