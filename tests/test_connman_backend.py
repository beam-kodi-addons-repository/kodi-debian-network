from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from resources.lib.backend.connman import (
    ConnManBackend,
    build_interactive_connect_script,
    extract_connman_error,
    parse_services_output,
    parse_technologies_output,
    prefix_length_to_netmask,
)
from resources.lib.backend.base import BackendUnavailableError
from resources.lib.models import IPv4Configuration, IPv4Mode, InterfaceKind, NetworkProfile, NetworkSnapshot


class FakeInteractiveIO:
    """Feeds canned connmanctl output chunks to `_run_prompt_driver` and
    records the lines written back, without touching real subprocess/pty IO.
    """

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = iter(chunks)
        self.written_lines: list[str] = []
        self.finished = False

    def write_line(self, line: str) -> None:
        self.written_lines.append(line)

    def read_chunk(self) -> str:
        try:
            return next(self._chunks)
        except StopIteration:
            self.finished = True
            return ""

    def is_finished(self) -> bool:
        return self.finished


TECHNOLOGIES_OUTPUT = """/net/connman/technology/wifi
  Name = WiFi
  Type = wifi
  Powered = True
  Connected = True
  Address = 02:11:22:33:44:55
/net/connman/technology/ethernet
  Name = Ethernet
  Type = ethernet
  Powered = False
  Connected = False
  Address = 02:aa:bb:cc:dd:ee
"""


SERVICES_OUTPUT = """*AO Home Network            wifi_aa:bb:cc:dd:ee:ff_486f6d65_psk
    Guest Network           wifi_aa:bb:cc:dd:ee:ff_4775657374_psk
    Wired                   ethernet_001122334455_cable
"""


class ConnManParserTests(unittest.TestCase):
    def test_parse_technologies_output(self) -> None:
        technologies = parse_technologies_output(TECHNOLOGIES_OUTPUT)

        self.assertEqual(set(technologies), {InterfaceKind.WIFI, InterfaceKind.ETHERNET})
        self.assertTrue(technologies[InterfaceKind.WIFI].powered)
        self.assertFalse(technologies[InterfaceKind.ETHERNET].connected)
        self.assertEqual(technologies[InterfaceKind.WIFI].mac_address, "02:11:22:33:44:55")

    def test_parse_services_output(self) -> None:
        services = parse_services_output(SERVICES_OUTPUT)

        self.assertEqual(len(services), 3)
        self.assertEqual(services[0].name, "Home Network")
        self.assertTrue(services[0].connected)
        self.assertTrue(services[0].autoconnect)
        self.assertEqual(services[0].security, ("psk",))
        self.assertEqual(services[2].kind, InterfaceKind.ETHERNET)

    def test_prefix_length_to_netmask(self) -> None:
        self.assertEqual(prefix_length_to_netmask(24), "255.255.255.0")
        self.assertEqual(prefix_length_to_netmask(16), "255.255.0.0")

    def test_build_interactive_connect_script(self) -> None:
        script = build_interactive_connect_script("wifi_test_psk", "secret")

        self.assertEqual(script, "agent on\nconnect wifi_test_psk\nsecret\nquit\n")

    def test_extract_connman_error(self) -> None:
        output = "Agent registered\nError /net/connman/service/test: invalid-key\n"
        self.assertEqual(extract_connman_error(output), "Error /net/connman/service/test: invalid-key")


class ConnManRunErrorDetectionTests(unittest.TestCase):
    def test_run_raises_on_textual_error_with_zero_exit_code(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        with mock.patch.object(ConnManBackend, "is_tooling_available", return_value=True), mock.patch(
            "resources.lib.backend.connman.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["connmanctl", "connect", "wifi_test"], 0, "Error /net/connman/service/wifi_test: Not registered\n", ""
            ),
        ):
            with self.assertRaises(BackendUnavailableError):
                backend._run("connect", "wifi_test")

    def test_run_passes_through_clean_output(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        with mock.patch.object(ConnManBackend, "is_tooling_available", return_value=True), mock.patch(
            "resources.lib.backend.connman.subprocess.run",
            return_value=subprocess.CompletedProcess(["connmanctl", "services"], 0, SERVICES_OUTPUT, ""),
        ):
            result = backend._run("services")

        self.assertEqual(result, SERVICES_OUTPUT)


class ConnManPromptDriverTests(unittest.TestCase):
    """Exercises the connmanctl prompt state machine directly, decoupled
    from the real pty/subprocess plumbing in `_drive_interactive_connect`.
    """

    def test_prompt_driver_succeeds_with_correct_password(self) -> None:
        io = FakeInteractiveIO(
            [
                "Error getting VPN connections: The name net.connman.vpn was not provided\n",
                "Agent registered\n",
                "Agent RequestInput wifi_test_psk\n",
                "Passphrase? \n",
                "Connected wifi_test_psk\n",
            ]
        )

        result = ConnManBackend._run_prompt_driver(
            io.write_line, io.read_chunk, io.is_finished, "wifi_test_psk", "secret"
        )

        self.assertIn("Connected wifi_test_psk", result)
        self.assertEqual(io.written_lines, ["agent on", "connect wifi_test_psk", "secret", "quit"])

    def test_prompt_driver_raises_and_aborts_on_invalid_password(self) -> None:
        io = FakeInteractiveIO(
            [
                "Error getting VPN connections: The name net.connman.vpn was not provided\n",
                "Agent registered\n",
                "Agent RequestInput wifi_test_psk\n",
                "Passphrase? \n",
                "Agent ReportError wifi_test_psk\n",
                "  invalid-key\n",
                "Retry (yes/no)? \n",
            ]
        )

        with self.assertRaises(BackendUnavailableError):
            ConnManBackend._run_prompt_driver(
                io.write_line, io.read_chunk, io.is_finished, "wifi_test_psk", "secret"
            )

        self.assertEqual(io.written_lines[-2:], ["no", "quit"])

    def test_prompt_driver_does_not_false_positive_on_vpn_startup_banner(self) -> None:
        # connmanctl always prints this on startup; it must not be mistaken
        # for a real connect failure (this is the exact bug reproduced live
        # against a Raspberry Pi: the VPN banner from "agent on" was bleeding
        # into the error check for the subsequent "connect" command).
        io = FakeInteractiveIO(
            [
                "Error getting VPN connections: The name net.connman.vpn was not provided\n",
                "Agent registered\n",
                "Agent RequestInput wifi_test_psk\n",
                "Passphrase? \n",
                "Connected wifi_test_psk\n",
            ]
        )

        result = ConnManBackend._run_prompt_driver(
            io.write_line, io.read_chunk, io.is_finished, "wifi_test_psk", "secret"
        )

        self.assertIn("Connected", result)


class ConnManConnectTests(unittest.TestCase):
    def test_connect_wifi_falls_back_to_interactive_passphrase(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        profile = NetworkProfile(
            service_id="wifi_test_psk",
            ssid="Test",
            password="secret",
            autoconnect=True,
            ipv4=IPv4Configuration(mode=IPv4Mode.DHCP),
        )
        snapshot = NetworkSnapshot(
            wifi_enabled=True,
            ethernet_enabled=False,
            active_service_id="wifi_test_psk",
        )

        with mock.patch.object(ConnManBackend, "is_tooling_available", return_value=True), mock.patch(
            "resources.lib.backend.connman.subprocess.run",
            return_value=subprocess.CompletedProcess(["connmanctl", "connect", "wifi_test_psk"], 0, "Error invalid-key\n", ""),
        ), mock.patch.object(
            backend, "_drive_interactive_connect", return_value="Connected wifi_test_psk\n"
        ) as drive_mock, mock.patch.object(
            backend, "_apply_profile_config"
        ) as apply_config_mock, mock.patch.object(
            backend,
            "snapshot",
            return_value=snapshot,
        ):
            result = backend.connect_wifi(profile)

        self.assertEqual(result, snapshot)
        drive_mock.assert_called_once_with("wifi_test_psk", "secret")
        apply_config_mock.assert_called_once_with(profile)

    def test_connect_wifi_raises_when_interactive_connect_fails(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        profile = NetworkProfile(
            service_id="wifi_test_psk",
            ssid="Test",
            password="secret",
            autoconnect=True,
            ipv4=IPv4Configuration(mode=IPv4Mode.STATIC, address="10.0.0.10", prefix_length=24, gateway="10.0.0.1"),
        )

        with mock.patch.object(ConnManBackend, "is_tooling_available", return_value=True), mock.patch(
            "resources.lib.backend.connman.subprocess.run",
            return_value=subprocess.CompletedProcess(["connmanctl", "connect", "wifi_test_psk"], 0, "Error invalid-key\n", ""),
        ), mock.patch.object(
            backend, "_drive_interactive_connect", side_effect=BackendUnavailableError("Invalid passphrase")
        ), mock.patch.object(backend, "_apply_profile_config") as apply_config_mock:
            with self.assertRaises(BackendUnavailableError):
                backend.connect_wifi(profile)

        apply_config_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()