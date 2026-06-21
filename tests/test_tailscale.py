from __future__ import annotations

import json
import unittest

from resources.lib.tailscale import TailscaleStatus, parse_status_json, parse_status_text


STATUS_JSON = json.dumps(
    {
        "BackendState": "Running",
        "Self": {
            "HostName": "example-host-a",
            "TailscaleIPs": ["100.64.0.1"],
            "OS": "linux",
            "Online": True,
            "ExitNodeOption": True,
        },
        "Peer": {
            "nodekey:1": {
                "HostName": "example-host-b",
                "TailscaleIPs": ["100.64.0.2"],
                "OS": "windows",
                "Online": False,
            },
            "nodekey:2": {
                "HostName": "example-host-c",
                "TailscaleIPs": ["100.64.0.3"],
                "OS": "linux",
                "Online": True,
                "ExitNodeOption": True,
            },
            "nodekey:3": {
                "HostName": "example-host-d",
                "TailscaleIPs": ["100.64.0.4"],
                "OS": "android",
                "Online": False,
            },
        },
        "Health": [
            "Tailscale can't reach the configured DNS servers. Internet connectivity may be affected.",
        ],
    }
)


class TailscaleStatusParsingTests(unittest.TestCase):
    def test_parses_self_and_peers(self) -> None:
        status = parse_status_json(STATUS_JSON)

        self.assertIsInstance(status, TailscaleStatus)
        self.assertTrue(status.installed)
        self.assertTrue(status.running)
        self.assertEqual(status.backend_state, "Running")
        self.assertEqual(len(status.peers), 4)

        by_hostname = {peer.hostname: peer for peer in status.peers}
        self_peer = by_hostname["example-host-a"]
        self.assertTrue(self_peer.is_self)
        self.assertTrue(self_peer.online)
        self.assertEqual(self_peer.ips, ("100.64.0.1",))
        self.assertTrue(self_peer.exit_node_option)

        offline_peer = by_hostname["example-host-b"]
        self.assertFalse(offline_peer.online)
        self.assertFalse(offline_peer.is_self)
        self.assertEqual(offline_peer.os, "windows")

        self.assertEqual(
            status.health,
            ("Tailscale can't reach the configured DNS servers. Internet connectivity may be affected.",),
        )

    def test_self_peer_sorts_first(self) -> None:
        status = parse_status_json(STATUS_JSON)
        self.assertTrue(status.peers[0].is_self)
        self.assertEqual(
            [peer.hostname for peer in status.peers[1:]],
            ["example-host-b", "example-host-c", "example-host-d"],
        )

    def test_parse_status_text_live_sample(self) -> None:
        # Uses placeholder IPs/hostnames (RFC 3849 documentation prefix for
        # IPv6), not data from any real tailnet.
        output = (
            "100.64.0.1      example-host-a       alice  linux   "
            "active; offers exit node; direct [2001:db8::1]:41641, tx 292 rx 220\n"
            "100.64.0.2      example-host-b       alice  windows -\n"
        )

        status_by_ip = parse_status_text(output)

        self.assertEqual(
            status_by_ip["100.64.0.1"],
            "active; offers exit node; direct [2001:db8::1]:41641, tx 292 rx 220",
        )
        self.assertEqual(status_by_ip["100.64.0.2"], "-")

    def test_parse_status_json_merges_status_text_by_ip(self) -> None:
        status_by_ip = {
            "100.64.0.1": "active; offers exit node; direct [2001:db8::1]:41641, tx 292 rx 220",
            "100.64.0.2": "-",
        }

        status = parse_status_json(STATUS_JSON, status_by_ip)

        by_hostname = {peer.hostname: peer for peer in status.peers}
        self.assertEqual(
            by_hostname["example-host-a"].status_text,
            "active; offers exit node; direct [2001:db8::1]:41641, tx 292 rx 220",
        )
        self.assertEqual(by_hostname["example-host-b"].status_text, "-")
        self.assertIsNone(by_hostname["example-host-c"].status_text)


if __name__ == "__main__":
    unittest.main()
