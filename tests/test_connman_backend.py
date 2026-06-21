from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from resources.lib.backend.connman import (
    ConnManBackend,
    ServiceEntry,
    band_from_frequency,
    build_interactive_connect_script,
    classify_wifi_security,
    extract_connman_error,
    netmask_to_prefix_length,
    parse_iw_dev_interface,
    parse_iw_link_bssid,
    parse_iw_scan_output,
    parse_service_detail_output,
    parse_services_output,
    parse_technologies_output,
    prefix_length_to_netmask,
)
from resources.lib.backend.base import BackendUnavailableError
from resources.lib.models import IPv4Configuration, IPv4Mode, InterfaceKind, NetworkProfile, NetworkSnapshot


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


SERVICE_DETAIL_OUTPUT = """/net/connman/service/ethernet_b827eb1cd54c_cable
  Type = ethernet
  Security = [  ]
  State = online
  Favorite = True
  Immutable = False
  AutoConnect = True
  Name = Wired
  Ethernet = [ Method=auto, Interface=eth0, Address=B8:27:EB:1C:D5:4C, MTU=1500 ]
  IPv4 = [ Method=dhcp, Address=10.188.35.25, Netmask=255.255.255.0, Gateway=10.188.35.1 ]
  IPv4.Configuration = [ Method=dhcp ]
  IPv6 = [  ]
  Nameservers = [ 10.188.35.1 ]
"""


IW_DEV_OUTPUT = """phy#0
\tUnnamed/non-netdev interface
\t\twdev 0x5
\t\taddr ba:27:eb:49:80:19
\t\ttype P2P-device
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr b8:27:eb:49:80:19
\t\ttype managed
"""


IW_SCAN_OUTPUT = """BSS 50:eb:f8:19:55:10(on wlan0)
\tcapability: ESS Privacy ShortPreamble ShortSlotTime (0x0431)
\tfreq: 2437
\tSSID: SSID Beam
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK FT/PSK PSK/SHA-256 SAE FT/SAE
BSS 52:eb:f8:19:55:10(on wlan0)
\tcapability: ESS Privacy ShortPreamble ShortSlotTime (0x0431)
\tfreq: 5180
\tSSID: Beam
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK FT/PSK PSK/SHA-256 SAE FT/SAE
BSS 56:eb:f8:19:55:10(on wlan0)
\tcapability: ESS Privacy ShortPreamble ShortSlotTime (0x0431)
\tfreq: 2462
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK
"""


IW_LINK_OUTPUT = """Connected to 56:eb:f8:19:55:10 (on wlan0)
\tSSID:
\tfreq: 2462
\tsignal: -50 dBm
\ttx bitrate: 65.0 MBit/s
"""


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

    def test_favorite_only_service_is_remembered_but_not_connected(self) -> None:
        # "*" means Favorite (saved), not currently connected -- that's "R"/"O".
        # Reproduces a live bug where a merely-saved network was shown as
        # "connected" in the UI.
        services = parse_services_output("*   SSID Beam            wifi_aa_53534944204265616d_psk\n")

        self.assertFalse(services[0].connected)
        self.assertTrue(services[0].remembered)

    def test_ready_service_is_connected(self) -> None:
        services = parse_services_output("  R Some Network         wifi_aa_536f6d65_psk\n")

        self.assertTrue(services[0].connected)

    def test_prefix_length_to_netmask(self) -> None:
        self.assertEqual(prefix_length_to_netmask(24), "255.255.255.0")
        self.assertEqual(prefix_length_to_netmask(16), "255.255.0.0")

    def test_netmask_to_prefix_length_roundtrip(self) -> None:
        self.assertEqual(netmask_to_prefix_length("255.255.255.0"), 24)
        self.assertEqual(netmask_to_prefix_length(prefix_length_to_netmask(16)), 16)

    def test_build_interactive_connect_script(self) -> None:
        script = build_interactive_connect_script("wifi_test_psk", "secret")

        self.assertEqual(script, "agent on\nconnect wifi_test_psk\nsecret\nquit\n")

    def test_extract_connman_error(self) -> None:
        output = "Agent registered\nError /net/connman/service/test: invalid-key\n"
        self.assertEqual(extract_connman_error(output), "Error /net/connman/service/test: invalid-key")

    def test_parse_service_detail_output_live_sample(self) -> None:
        detail = parse_service_detail_output("ethernet_b827eb1cd54c_cable", SERVICE_DETAIL_OUTPUT)

        self.assertEqual(detail.address, "10.188.35.25")
        self.assertEqual(detail.prefix_length, 24)
        self.assertEqual(detail.gateway, "10.188.35.1")
        self.assertEqual(detail.nameservers, ("10.188.35.1",))
        self.assertEqual(detail.ipv4_method, "dhcp")

    def test_parse_iw_dev_interface(self) -> None:
        self.assertEqual(parse_iw_dev_interface(IW_DEV_OUTPUT), "wlan0")

    def test_parse_iw_scan_output_live_sample(self) -> None:
        entries = parse_iw_scan_output(IW_SCAN_OUTPUT)

        self.assertEqual(len(entries), 3)
        by_bssid = {entry.bssid: entry for entry in entries}

        # "SSID Beam" and "Beam" both advertise PSK *and* SAE -- a WPA2/WPA3
        # transition-mode network -- the exact live config that broke
        # connections on this Pi 3's WiFi chip (no SAE/PMF support).
        self.assertEqual(by_bssid["50:eb:f8:19:55:10"].ssid, "SSID Beam")
        self.assertEqual(by_bssid["50:eb:f8:19:55:10"].security_label, "WPA2/WPA3")
        self.assertEqual(by_bssid["52:eb:f8:19:55:10"].security_label, "WPA2/WPA3")

        # The hidden BSS broadcasts no SSID line at all, but is plain
        # WPA2-PSK (no SAE) -- distinguishable only via raw `iw scan`.
        hidden = by_bssid["56:eb:f8:19:55:10"]
        self.assertEqual(hidden.ssid, "")
        self.assertEqual(hidden.security_label, "WPA2")

        self.assertEqual(by_bssid["50:eb:f8:19:55:10"].band, "2.4GHz")
        self.assertEqual(by_bssid["52:eb:f8:19:55:10"].band, "5GHz")
        self.assertEqual(hidden.band, "2.4GHz")

    def test_band_from_frequency(self) -> None:
        self.assertEqual(band_from_frequency(2412), "2.4GHz")
        self.assertEqual(band_from_frequency(5180), "5GHz")
        self.assertEqual(band_from_frequency(5980), "6GHz")

    def test_parse_iw_link_bssid_live_sample(self) -> None:
        self.assertEqual(parse_iw_link_bssid(IW_LINK_OUTPUT), "56:eb:f8:19:55:10")

    def test_parse_iw_link_bssid_not_connected(self) -> None:
        self.assertIsNone(parse_iw_link_bssid("Not connected.\n"))

    def test_classify_wifi_security_open_network(self) -> None:
        block = "BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tcapability: ESS ShortSlotTime (0x0421)\n"
        self.assertEqual(classify_wifi_security(block), "Open")

    def test_classify_wifi_security_wep(self) -> None:
        block = "BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tcapability: ESS Privacy ShortSlotTime (0x0431)\n"
        self.assertEqual(classify_wifi_security(block), "WEP")


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


class ConnManSnapshotIpv4Tests(unittest.TestCase):
    def test_ipv4_for_kind_populates_real_address_for_connected_service(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        services = (
            ServiceEntry(
                service_id="ethernet_b827eb1cd54c_cable",
                name="Wired",
                kind=InterfaceKind.ETHERNET,
                flags="*AO",
                security=(),
            ),
        )
        with mock.patch.object(backend, "_service_detail") as detail_mock:
            from resources.lib.backend.connman import parse_service_detail_output

            detail_mock.return_value = parse_service_detail_output(
                "ethernet_b827eb1cd54c_cable", SERVICE_DETAIL_OUTPUT
            )
            ipv4 = backend._ipv4_for_kind(InterfaceKind.ETHERNET, services)

        self.assertEqual(ipv4.address, "10.188.35.25")
        self.assertEqual(ipv4.prefix_length, 24)
        self.assertEqual(ipv4.gateway, "10.188.35.1")
        self.assertEqual(ipv4.dns_servers, ("10.188.35.1",))

    def test_ipv4_for_kind_returns_empty_dhcp_when_not_connected(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        services = (
            ServiceEntry(
                service_id="ethernet_b827eb1cd54c_cable",
                name="Wired",
                kind=InterfaceKind.ETHERNET,
                flags="",
                security=(),
            ),
        )
        ipv4 = backend._ipv4_for_kind(InterfaceKind.ETHERNET, services)

        self.assertEqual(ipv4.mode, IPv4Mode.DHCP)
        self.assertIsNone(ipv4.address)


class ConnManAccessPointsHiddenBssidTests(unittest.TestCase):
    def test_connected_hidden_network_reports_only_its_own_bssid(self) -> None:
        # Several BSSIDs can share the same (hidden) SSID -- e.g. a mesh --
        # so once connected we must report the single BSSID actually
        # associated with, not the whole candidate list.
        backend = ConnManBackend(executable="connmanctl")
        services = (
            ServiceEntry(
                service_id="wifi_hidden_psk",
                name="",
                kind=InterfaceKind.WIFI,
                flags="*AO",
                security=("psk",),
            ),
        )
        scan_entries = parse_iw_scan_output(IW_SCAN_OUTPUT)
        hidden_entries = tuple(entry for entry in scan_entries if not entry.ssid)

        with mock.patch.object(backend, "_services", return_value=services), mock.patch.object(
            backend, "_scan_entries", return_value=hidden_entries
        ), mock.patch.object(backend, "_wifi_interface_name", return_value="wlan0"), mock.patch.object(
            backend, "_connected_bssid", return_value="56:eb:f8:19:55:10"
        ):
            access_points = backend._access_points()

        self.assertEqual(len(access_points), 1)
        self.assertEqual(access_points[0].bssid, "56:eb:f8:19:55:10")

    def test_unconnected_hidden_network_lists_all_candidate_bssids(self) -> None:
        backend = ConnManBackend(executable="connmanctl")
        services = (
            ServiceEntry(
                service_id="wifi_hidden_psk",
                name="",
                kind=InterfaceKind.WIFI,
                flags="",
                security=("psk",),
            ),
        )
        scan_entries = parse_iw_scan_output(IW_SCAN_OUTPUT)
        hidden_entries = tuple(entry for entry in scan_entries if not entry.ssid)

        with mock.patch.object(backend, "_services", return_value=services), mock.patch.object(
            backend, "_scan_entries", return_value=hidden_entries
        ):
            access_points = backend._access_points()

        self.assertEqual(access_points[0].bssid, "56:eb:f8:19:55:10")


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
