from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from resources.lib.backend.networkmanager import (
    NetworkManagerBackend,
    band_from_frequency,
    combine_bands,
    parse_connections_output,
    parse_device_status_output,
    parse_ipv4_get_values,
    parse_wifi_list_output,
    split_terse_fields,
)
from resources.lib.backend.base import BackendUnavailableError
from resources.lib.models import IPv4Configuration, IPv4Mode, InterfaceKind, NetworkProfile


DEVICE_STATUS_OUTPUT = (
    "wlan0:wifi:connected:Home Network\n"
    "eth0:ethernet:unmanaged:--\n"
    "lo:loopback:unmanaged:--\n"
)


CONNECTIONS_OUTPUT = (
    "Home Network:11111111-1111-1111-1111-111111111111:802-11-wireless:yes:yes\n"
    "Guest Network:22222222-2222-2222-2222-222222222222:802-11-wireless:no:no\n"
    "Wired connection 1:33333333-3333-3333-3333-333333333333:802-3-ethernet:yes:no\n"
)


WIFI_LIST_OUTPUT = (
    r"*:Home Network:50\:eb\:f8\:19\:55\:10:WPA2:80:2437" "\n"
    r":Home Network:52\:eb\:f8\:19\:55\:10:WPA2:70:5180" "\n"
    r":Guest Network:60\:eb\:f8\:19\:55\:10:--:60:2462" "\n"
    r"::56\:eb\:f8\:19\:55\:10:WPA2:40:2462" "\n"
)


class SplitTerseFieldsTests(unittest.TestCase):
    def test_plain_fields(self) -> None:
        self.assertEqual(split_terse_fields("a:b:c"), ("a", "b", "c"))

    def test_unescapes_colons_within_a_field(self) -> None:
        self.assertEqual(
            split_terse_fields(r"*:Home:50\:eb\:f8\:19\:55\:10:WPA2"),
            ("*", "Home", "50:eb:f8:19:55:10", "WPA2"),
        )

    def test_empty_fields(self) -> None:
        self.assertEqual(split_terse_fields("::a:"), ("", "", "a", ""))


class ParseDeviceStatusOutputTests(unittest.TestCase):
    def test_parses_wifi_and_ethernet_and_skips_other_kinds(self) -> None:
        statuses = parse_device_status_output(DEVICE_STATUS_OUTPUT)
        self.assertEqual(len(statuses), 2)

        wifi = next(s for s in statuses if s.kind is InterfaceKind.WIFI)
        self.assertEqual(wifi.device, "wlan0")
        self.assertEqual(wifi.state, "connected")
        self.assertEqual(wifi.connection, "Home Network")

        ethernet = next(s for s in statuses if s.kind is InterfaceKind.ETHERNET)
        self.assertEqual(ethernet.device, "eth0")
        self.assertEqual(ethernet.state, "unmanaged")
        self.assertIsNone(ethernet.connection)


class ParseConnectionsOutputTests(unittest.TestCase):
    def test_parses_wifi_and_ethernet_profiles(self) -> None:
        # `connection show` (the list form) only exposes generic fields --
        # type-specific properties like 802-11-wireless.ssid are invalid
        # here, so parse_connections_output never fills in ssid itself.
        connections = parse_connections_output(CONNECTIONS_OUTPUT)
        self.assertEqual(len(connections), 3)
        self.assertTrue(all(c.ssid is None for c in connections))

        home = next(c for c in connections if c.name == "Home Network")
        self.assertEqual(home.kind, InterfaceKind.WIFI)
        self.assertTrue(home.autoconnect)
        self.assertTrue(home.active)

        guest = next(c for c in connections if c.name == "Guest Network")
        self.assertFalse(guest.autoconnect)
        self.assertFalse(guest.active)

        wired = next(c for c in connections if c.kind is InterfaceKind.ETHERNET)
        self.assertIsNone(wired.ssid)


class BackendConnectionsTests(unittest.TestCase):
    def test_connections_resolves_ssid_per_wifi_profile_with_a_separate_call(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_run") as run:
            run.side_effect = [CONNECTIONS_OUTPUT, "Home Network\n", "Guest Network\n"]
            connections = backend._connections()

        run.assert_any_call(
            "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT,ACTIVE", "connection", "show"
        )
        run.assert_any_call(
            "-g", "802-11-wireless.ssid", "connection", "show", "11111111-1111-1111-1111-111111111111"
        )
        home = next(c for c in connections if c.name == "Home Network")
        self.assertEqual(home.ssid, "Home Network")
        wired = next(c for c in connections if c.kind is InterfaceKind.ETHERNET)
        self.assertIsNone(wired.ssid)


class ParseWifiListOutputTests(unittest.TestCase):
    def test_parses_bssid_security_signal_and_band(self) -> None:
        entries = parse_wifi_list_output(WIFI_LIST_OUTPUT)
        self.assertEqual(len(entries), 4)

        connected = next(e for e in entries if e.in_use)
        self.assertEqual(connected.ssid, "Home Network")
        self.assertEqual(connected.bssid, "50:eb:f8:19:55:10")
        self.assertEqual(connected.security_label, "WPA2")
        self.assertEqual(connected.signal, 80)
        self.assertEqual(connected.band, "2.4GHz")

    def test_open_network_has_no_security_label(self) -> None:
        entries = parse_wifi_list_output(WIFI_LIST_OUTPUT)
        guest = next(e for e in entries if e.ssid == "Guest Network")
        self.assertIsNone(guest.security_label)

    def test_hidden_network_has_blank_ssid(self) -> None:
        entries = parse_wifi_list_output(WIFI_LIST_OUTPUT)
        hidden = [e for e in entries if not e.ssid]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].bssid, "56:eb:f8:19:55:10")


class ParseIpv4GetValuesTests(unittest.TestCase):
    def test_dhcp(self) -> None:
        method, address, prefix_length, gateway, dns_servers = parse_ipv4_get_values("auto\n\n\n\n")
        self.assertEqual(method, "auto")
        self.assertIsNone(address)
        self.assertIsNone(prefix_length)
        self.assertIsNone(gateway)
        self.assertEqual(dns_servers, ())

    def test_static(self) -> None:
        output = "manual\n10.0.0.5/24\n10.0.0.1\n1.1.1.1,8.8.8.8\n"
        method, address, prefix_length, gateway, dns_servers = parse_ipv4_get_values(output)
        self.assertEqual(method, "manual")
        self.assertEqual(address, "10.0.0.5")
        self.assertEqual(prefix_length, 24)
        self.assertEqual(gateway, "10.0.0.1")
        self.assertEqual(dns_servers, ("1.1.1.1", "8.8.8.8"))


class BandHelperTests(unittest.TestCase):
    def test_band_from_frequency(self) -> None:
        self.assertEqual(band_from_frequency(2437), "2.4GHz")
        self.assertEqual(band_from_frequency(5180), "5GHz")
        self.assertEqual(band_from_frequency(6000), "6GHz")

    def test_combine_bands_orders_and_dedupes(self) -> None:
        self.assertEqual(combine_bands(["5GHz", "2.4GHz", "5GHz"]), "2.4GHz/5GHz")
        self.assertIsNone(combine_bands([None, None]))


class RunErrorDetectionTests(unittest.TestCase):
    def test_nonzero_exit_raises_with_stderr_message(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "is_tooling_available", return_value=True):
            with mock.patch(
                "resources.lib.backend.networkmanager.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="Error: Connection activation failed"
                ),
            ):
                with self.assertRaises(BackendUnavailableError) as ctx:
                    backend._run("connection", "up", "does-not-exist")
        self.assertIn("Connection activation failed", str(ctx.exception))

    def test_missing_executable_raises(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "is_tooling_available", return_value=False):
            with self.assertRaises(BackendUnavailableError):
                backend._run("device", "status")


class BackendCommandTests(unittest.TestCase):
    def test_set_interface_enabled_wifi_uses_radio_toggle(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot") as snapshot:
            run.return_value = ""
            backend.set_interface_enabled(InterfaceKind.WIFI, True)
        run.assert_called_once_with("radio", "wifi", "on")
        snapshot.assert_called_once()

    def test_set_interface_enabled_ethernet_uses_device_connect(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_device_statuses") as statuses, \
                mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot") as snapshot:
            statuses.return_value = parse_device_status_output(DEVICE_STATUS_OUTPUT)
            backend.set_interface_enabled(InterfaceKind.ETHERNET, False)
        run.assert_called_once_with("device", "disconnect", "eth0")
        snapshot.assert_called_once()

    def test_disconnect_saved_connection(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot"):
            backend.disconnect("11111111-1111-1111-1111-111111111111")
        run.assert_called_once_with("connection", "down", "11111111-1111-1111-1111-111111111111")

    def test_disconnect_unsaved_network_is_a_noop_command(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot"):
            backend.disconnect("wifi:56:eb:f8:19:55:10")
        run.assert_not_called()

    def test_forget_wifi_deletes_connection(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot"):
            backend.forget_wifi("22222222-2222-2222-2222-222222222222")
        run.assert_called_once_with("connection", "delete", "22222222-2222-2222-2222-222222222222")

    def test_connect_wifi_to_saved_profile_brings_connection_up(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        profile = NetworkProfile(service_id="11111111-1111-1111-1111-111111111111", ssid="Home Network")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "snapshot"):
            backend.connect_wifi(profile)
        run.assert_any_call("connection", "up", "11111111-1111-1111-1111-111111111111")

    def test_connect_wifi_to_new_network_uses_device_wifi_connect(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        profile = NetworkProfile(service_id="wifi:56:eb:f8:19:55:10", ssid="New Network", password="secret")
        with mock.patch.object(NetworkManagerBackend, "_run") as run, \
                mock.patch.object(NetworkManagerBackend, "_wifi_iface", return_value="wlan0"), \
                mock.patch.object(NetworkManagerBackend, "_find_connection_uuid_by_ssid", return_value=None), \
                mock.patch.object(NetworkManagerBackend, "_device_statuses", return_value=()), \
                mock.patch.object(NetworkManagerBackend, "snapshot"):
            backend.connect_wifi(profile)
        run.assert_any_call(
            "device", "wifi", "connect", "New Network", "hidden", "yes", "password", "secret", "ifname", "wlan0"
        )

    def test_apply_profile_config_static_ip_requires_full_address(self) -> None:
        backend = NetworkManagerBackend(executable="nmcli")
        profile = NetworkProfile(
            service_id="11111111-1111-1111-1111-111111111111",
            ssid="Home Network",
            ipv4=IPv4Configuration(mode=IPv4Mode.STATIC),
        )
        with self.assertRaises(BackendUnavailableError):
            backend._apply_profile_config(profile.service_id, profile)


if __name__ == "__main__":
    unittest.main()
