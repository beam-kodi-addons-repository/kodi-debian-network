from __future__ import annotations

import sys
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

try:  # pragma: no cover - only available inside Kodi runtime
    import xbmc  # type: ignore
    import xbmcaddon  # type: ignore
    import xbmcgui  # type: ignore
    import xbmcplugin  # type: ignore
    import xbmcvfs  # type: ignore
except ImportError:  # pragma: no cover - exercised only outside Kodi
    xbmc = None
    xbmcaddon = None
    xbmcgui = None
    xbmcplugin = None
    xbmcvfs = None

from . import tailscale
from .backend.base import BackendUnavailableError
from .backend.factory import build_backend
from .models import IPv4Configuration, IPv4Mode, NetworkSnapshot, NetworkProfile
from .system_integration import detect_bootstrap_status, run_system_install


DEFAULT_HELPER_SOCKET = "/run/kodi-network-helper.sock"
BACKEND_MODE_MAP = {
    "0": "auto",
    "1": "helper",
    "2": "demo",
    "auto": "auto",
    "helper": "helper",
    "demo": "demo",
}


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: str
    params: dict[str, str]
    folder: bool = True
    color: str | None = None
    context_menu: tuple[tuple[str, str, dict[str, str]], ...] = ()


class NetworkAssistantApp:
    def __init__(self, argv: list[str] | None = None) -> None:
        if xbmcaddon is None or xbmcgui is None or xbmcplugin is None:
            raise RuntimeError("Kodi runtime is required to run the add-on UI")

        self.argv = argv or sys.argv
        self.handle = self._parse_handle(self.argv)
        self.addon = xbmcaddon.Addon()
        self.addon_path = self._addon_path()
        self.helper_socket = self._setting("helper_socket") or DEFAULT_HELPER_SOCKET
        self.backend_mode = self._selected_backend_mode()
        self.bootstrap_status = detect_bootstrap_status(self.addon_path)
        self._bootstrap_prompt_shown = False
        self.backend = build_backend(self.backend_mode, self.helper_socket)

    @staticmethod
    def _parse_handle(argv: list[str]) -> int:
        try:
            return int(argv[1])
        except Exception:
            return -1

    def _setting(self, setting_id: str) -> str:
        getter = getattr(self.addon, "getSettingString", None)
        if callable(getter):
            return getter(setting_id)
        getter = getattr(self.addon, "getSetting", None)
        if callable(getter):
            return getter(setting_id)
        return ""

    def _selected_backend_mode(self) -> str:
        getter = getattr(self.addon, "getSettingInt", None)
        if callable(getter):
            raw_mode = str(getter("backend_mode"))
        else:
            raw_mode = self._setting("backend_mode") or "0"
        raw_mode = raw_mode.strip().lower()
        return BACKEND_MODE_MAP.get(raw_mode, "auto")

    def _addon_path(self) -> str:
        path = self.addon.getAddonInfo("path")
        if xbmcvfs is not None and hasattr(xbmcvfs, "translatePath"):
            return xbmcvfs.translatePath(path)
        return path

    def _label(self, string_id: int, fallback: str) -> str:
        text = self.addon.getLocalizedString(string_id)
        return text or fallback

    def _params(self) -> dict[str, str]:
        query = ""
        if len(self.argv) > 2:
            query = self.argv[2].lstrip("?")
        return dict(parse_qsl(query, keep_blank_values=True))

    def _param(self, key: str, default: str = "") -> str:
        return self._params().get(key, default)

    def _url(self, action: str, **params: str) -> str:
        query = urlencode({"action": action, **params})
        return f"{self.argv[0]}?{query}"

    def _refresh_container(self) -> None:
        if xbmc is not None:
            xbmc.executebuiltin("Container.Refresh")

    def _ssid_label(self, ssid: str) -> str:
        return ssid or self._label(30043, "Hidden network")

    def _notify(self, title: str, message: str, level: str = "info") -> None:
        icon = xbmcgui.NOTIFICATION_INFO
        if level == "warning":
            icon = xbmcgui.NOTIFICATION_WARNING
        elif level == "error":
            icon = xbmcgui.NOTIFICATION_ERROR
        xbmcgui.Dialog().notification(title, message, icon=icon, time=3500)

    def _show_text(self, title: str, body: str) -> None:
        dialog = xbmcgui.Dialog()
        if hasattr(dialog, "textviewer"):
            dialog.textviewer(title, body)
            return
        dialog.ok(title, body)

    def _snapshot(self) -> NetworkSnapshot:
        try:
            return self.backend.snapshot()
        except Exception as exc:
            return NetworkSnapshot(
                wifi_enabled=False,
                ethernet_enabled=False,
                message=str(exc),
            )

    def _render(self, title: str, items: list[MenuItem]) -> None:
        xbmcplugin.setPluginCategory(self.handle, title)
        xbmcplugin.setContent(self.handle, "files")
        for item in items:
            label = f"[COLOR {item.color}]{item.label}[/COLOR]" if item.color else item.label
            list_item = xbmcgui.ListItem(label=label)
            if item.context_menu:
                list_item.addContextMenuItems(
                    [
                        (menu_label, f"RunPlugin({self._url(menu_action, **menu_params)})")
                        for menu_label, menu_action, menu_params in item.context_menu
                    ]
                )
            xbmcplugin.addDirectoryItem(
                self.handle,
                self._url(item.action, **item.params),
                list_item,
                item.folder,
            )
        xbmcplugin.endOfDirectory(self.handle, succeeded=True, cacheToDisc=False)

    def run(self) -> None:
        action = self._param("action", "root")
        if action == "root" and self._maybe_prompt_bootstrap():
            return
        if action == "wifi":
            self.show_wifi()
            return
        if action == "refresh_wifi":
            self.refresh_wifi()
            return
        if action == "interfaces":
            self.show_interfaces()
            return
        if action == "refresh_interfaces":
            self.refresh_interfaces()
            return
        if action == "status":
            self.show_status()
            return
        if action == "tailscale":
            self.show_tailscale_status()
            return
        if action == "reload":
            self.reload_backend()
            return
        if action == "install_system":
            self.install_system_integration()
            return
        if action == "connect":
            self.connect_wifi()
            return
        if action == "disconnect_wifi":
            self.disconnect_wifi()
            return
        if action == "forget_wifi":
            self.forget_wifi()
            return
        if action == "wifi_profiles":
            self.show_wifi_profiles()
            return
        if action == "toggle_interface":
            self.toggle_interface()
            return
        if action == "toggle_tailscale":
            self.toggle_tailscale()
            return
        self.show_root()

    def reload_backend(self) -> None:
        self._refresh_bootstrap_status()
        self.backend = build_backend(self._selected_backend_mode(), self.helper_socket)
        self._notify(
            self._label(30000, "Network Assistant"),
            f"{self._label(30028, 'Backend')}: {self.backend.name}",
        )
        self.show_root()

    def show_root(self) -> None:
        self._refresh_bootstrap_status()
        snapshot = self._snapshot()
        items = [
            MenuItem(self._label(30007, "Wi-Fi networks"), "wifi", {}, True),
            MenuItem(self._label(30046, "Saved Wi-Fi profiles"), "wifi_profiles", {}, True),
            MenuItem(self._label(30008, "Interfaces"), "interfaces", {}, True),
            MenuItem(self._label(30009, "Status"), "status", {}, False),
        ]
        if tailscale.is_installed():
            items.append(MenuItem(self._label(30050, "Tailscale status"), "tailscale", {}, False))
        items += [
            MenuItem(self._label(30034, "Install / repair system integration"), "install_system", {}, False),
            MenuItem(self._label(30010, "Reload backend"), "reload", {}, False),
        ]
        if self.bootstrap_status.needs_bootstrap:
            items.insert(0, MenuItem(self._label(30041, "Bootstrap is required before network control will work"), "install_system", {}, False))
        if snapshot.message:
            items.insert(0, MenuItem(snapshot.message, "status", {}, False))
        self._render(self._label(30000, "Network Assistant"), items)

    def show_status(self) -> None:
        snapshot = self._snapshot()
        lines = [
            f"{self._label(30028, 'Backend')}: {self.backend.name}",
            f"{self._label(30030, 'Wi-Fi')}: {self._state_word(snapshot.wifi_enabled)}",
            f"{self._label(30031, 'Ethernet')}: {self._state_word(snapshot.ethernet_enabled)}",
        ]
        if snapshot.active_service_id:
            lines.append(f"{self._label(30029, 'Active service')}: {snapshot.active_service_id}")
        if snapshot.interfaces:
            lines.append("")
            lines.append(self._label(30008, "Interfaces"))
            for interface in snapshot.interfaces:
                suffix = " / connected" if interface.connected else ""
                color = "green" if interface.connected else "red" if not interface.enabled else "white"
                lines.append(
                    f"- [COLOR {color}]{interface.name} ({interface.kind.value}): "
                    f"{self._state_word(interface.enabled)}{suffix}[/COLOR]"
                )
                if interface.mac_address:
                    lines.append(f"    MAC: {interface.mac_address}")
                if interface.connected and interface.ipv4.address:
                    lines.append(f"    IPv4: {interface.ipv4.address}/{interface.ipv4.prefix_length or '?'}")
                    if interface.ipv4.gateway:
                        lines.append(f"    Gateway: {interface.ipv4.gateway}")
                    if interface.ipv4.dns_servers:
                        lines.append(f"    DNS: {', '.join(interface.ipv4.dns_servers)}")
        if snapshot.access_points:
            lines.append("")
            lines.append(self._label(30007, "Wi-Fi networks"))
            for access_point in snapshot.access_points:
                state = "connected" if access_point.connected else "saved" if access_point.remembered else "available"
                color = "green" if access_point.connected else "yellow" if access_point.remembered else "white"
                name = self._ssid_label(access_point.ssid)
                if not access_point.ssid and access_point.bssid:
                    name = f"{name} ({access_point.bssid})"
                security = f" {access_point.security_label}" if access_point.security_label else ""
                band = f" {access_point.band}" if access_point.band else ""
                lines.append(f"- [COLOR {color}]{name} [{access_point.signal}%]{security}{band} {state}[/COLOR]")
        if snapshot.message:
            lines.append("")
            lines.append(f"[COLOR red]{snapshot.message}[/COLOR]")
        self._show_text(self._label(30009, "Status"), "\n".join(lines))
        self.show_root()

    def show_tailscale_status(self) -> None:
        status = tailscale.get_status()
        lines = [
            f"{self._label(30028, 'Backend')}: {self._label(30051, 'Tailscale')}",
            f"{self._label(30052, 'Daemon state')}: {status.backend_state or self._state_word(status.running)}",
        ]
        if status.peers:
            lines.append("")
            for peer in status.peers:
                color = "green" if peer.online else "red"
                hostname = peer.hostname or self._label(30043, "Hidden network")
                if peer.is_self:
                    hostname = f"{hostname} ({self._label(30053, 'this device')})"
                ip = ", ".join(peer.ips) or "-"
                if peer.status_text:
                    state = peer.status_text
                else:
                    state = self._label(30054, "Online") if peer.online else self._label(30055, "Offline")
                    if peer.exit_node:
                        state = f"{state}, {self._label(30056, 'exit node')}"
                    elif peer.exit_node_option:
                        state = f"{state}, {self._label(30057, 'offers exit node')}"
                lines.append(f"- [COLOR {color}]{hostname}  {ip}  {state}[/COLOR]")
        if status.health:
            lines.append("")
            lines.append(self._label(30058, "Health"))
            for warning in status.health:
                lines.append(f"[COLOR red]- {warning}[/COLOR]")
        if status.message:
            lines.append("")
            lines.append(f"[COLOR red]{status.message}[/COLOR]")
        self._show_text(self._label(30050, "Tailscale status"), "\n".join(lines))
        self.show_root()

    def show_wifi(self) -> None:
        snapshot = self._snapshot()
        items: list[MenuItem] = [MenuItem(self._label(30011, "Rescan Wi-Fi"), "refresh_wifi", {}, False)]

        try:
            access_points = self.backend.scan_wifi() if snapshot.wifi_enabled else ()
        except Exception as exc:
            access_points = ()
            items.append(MenuItem(str(exc), "status", {}, False))

        if not access_points:
            items.append(MenuItem(self._label(30026, "No Wi-Fi networks found"), "root", {}, False))
        else:
            for access_point in access_points:
                name = self._ssid_label(access_point.ssid)
                if not access_point.ssid and access_point.bssid:
                    name = f"{name} ({access_point.bssid})"
                label_bits = [name, f"{access_point.signal}%"]
                if access_point.connected:
                    label_bits.append(self._label(30015, "Connected"))
                elif access_point.remembered:
                    label_bits.append("saved")
                if access_point.security_label:
                    label_bits.append(access_point.security_label)
                elif access_point.security:
                    label_bits.append("secured")
                if access_point.band:
                    label_bits.append(access_point.band)
                color = "green" if access_point.connected else "yellow" if access_point.remembered else None
                context_menu = []
                if access_point.connected:
                    context_menu.append(
                        (self._label(30044, "Disconnect"), "disconnect_wifi", {"service_id": access_point.service_id})
                    )
                if access_point.remembered:
                    context_menu.append(
                        (self._label(30045, "Forget network"), "forget_wifi", {"service_id": access_point.service_id})
                    )
                items.append(
                    MenuItem(
                        "  ".join(label_bits),
                        "connect",
                        {"service_id": access_point.service_id},
                        False,
                        color,
                        tuple(context_menu),
                    )
                )

        self._render(self._label(30007, "Wi-Fi networks"), items)

    def refresh_wifi(self) -> None:
        self._refresh_container()
        self.show_wifi()

    def show_interfaces(self) -> None:
        snapshot = self._snapshot()
        items: list[MenuItem] = [MenuItem(self._label(30012, "Refresh"), "refresh_interfaces", {}, False)]

        if not snapshot.interfaces:
            items.append(MenuItem(self._label(30027, "No interfaces found"), "root", {}, False))
        else:
            for interface in snapshot.interfaces:
                label = f"{interface.name}  {self._state_word(interface.enabled)}"
                if interface.connected:
                    label += "  connected"
                    if interface.ipv4.address:
                        label += f"  ({interface.ipv4.address})"
                color = "green" if interface.connected else "red" if not interface.enabled else None
                items.append(
                    MenuItem(
                        label,
                        "toggle_interface",
                        {"kind": interface.kind.value},
                        False,
                        color,
                    )
                )

        items.append(self._tailscale_menu_item())

        self._render(self._label(30008, "Interfaces"), items)

    def _tailscale_menu_item(self) -> MenuItem:
        if not tailscale.is_installed():
            return MenuItem(
                f"{self._label(30051, 'Tailscale')}  {self._label(30061, 'not installed')}",
                "root",
                {},
                False,
            )

        status = tailscale.get_status()
        word = self._label(30060, "on") if status.running else self._label(30059, "off")
        color = "green" if status.running else "red"
        return MenuItem(
            f"{self._label(30051, 'Tailscale')}  {word}",
            "toggle_tailscale",
            {},
            False,
            color,
        )

    def refresh_interfaces(self) -> None:
        self._refresh_container()
        self.show_interfaces()

    def connect_wifi(self) -> None:
        service_id = self._param("service_id")
        if not service_id:
            self._notify(self._label(30009, "Status"), "Missing Wi-Fi service id", level="error")
            self.show_wifi()
            return

        snapshot = self._snapshot()
        access_point = next((item for item in snapshot.access_points if item.service_id == service_id), None)
        dialog = xbmcgui.Dialog()
        ssid = access_point.ssid if access_point else service_id
        display_name = self._ssid_label(ssid)

        password = None
        needs_password = self._needs_password(access_point)
        if needs_password:
            password = dialog.input(
                self._label(30025, "Password"),
                type=xbmcgui.INPUT_ALPHANUM,
                option=xbmcgui.ALPHANUM_HIDE_INPUT,
            )
            if access_point and access_point.security and not access_point.remembered and not password:
                self.show_wifi()
                return

        autoconnect = dialog.yesno(
            self._label(30018, "Save profile and autoconnect?"),
            display_name,
        )
        mode_index = dialog.select(
            self._label(30022, "Network configuration"),
            [self._label(30019, "DHCP"), self._label(30020, "Static")],
        )
        if mode_index == -1:
            self.show_wifi()
            return

        if mode_index == 0:
            ipv4 = IPv4Configuration(mode=IPv4Mode.DHCP)
        else:
            address = dialog.input(self._label(30021, "IP address"))
            prefix_length = dialog.input(self._label(30032, "Prefix length"), type=xbmcgui.INPUT_NUMERIC)
            gateway = dialog.input(self._label(30023, "Gateway"))
            dns_servers = dialog.input(self._label(30024, "DNS servers (comma separated)"))
            ipv4 = IPv4Configuration(
                mode=IPv4Mode.STATIC,
                address=address or None,
                prefix_length=self._parse_int(prefix_length),
                gateway=gateway or None,
                dns_servers=self._parse_dns(dns_servers),
            )

        profile = NetworkProfile(
            service_id=service_id,
            ssid=ssid,
            password=password or None,
            autoconnect=autoconnect,
            ipv4=ipv4,
        )

        try:
            self.backend.connect_wifi(profile)
            self._notify(self._label(30017, "Connect / edit"), display_name)
        except (BackendUnavailableError, Exception) as exc:
            self._notify(self._label(30009, "Status"), str(exc), level="error")

        self._refresh_container()
        self.show_wifi()

    def disconnect_wifi(self) -> None:
        service_id = self._param("service_id")
        if not service_id:
            self.show_wifi()
            return

        try:
            self.backend.disconnect(service_id)
            self._notify(self._label(30009, "Status"), self._label(30049, "Disconnected"))
        except (BackendUnavailableError, Exception) as exc:
            self._notify(self._label(30009, "Status"), str(exc), level="error")

        self._refresh_container()
        self.show_wifi()

    def forget_wifi(self) -> None:
        service_id = self._param("service_id")
        if not service_id:
            self.show_wifi()
            return

        dialog = xbmcgui.Dialog()
        if not dialog.yesno(self._label(30045, "Forget network"), self._label(30047, "Forget this saved network?")):
            self.show_wifi()
            return

        try:
            self.backend.forget_wifi(service_id)
        except (BackendUnavailableError, Exception) as exc:
            self._notify(self._label(30009, "Status"), str(exc), level="error")

        self._refresh_container()
        self.show_wifi()

    def show_wifi_profiles(self) -> None:
        snapshot = self._snapshot()
        items: list[MenuItem] = []
        saved = [item for item in snapshot.access_points if item.remembered]

        if not saved:
            items.append(MenuItem(self._label(30048, "No saved networks"), "root", {}, False))
        else:
            for access_point in saved:
                name = self._ssid_label(access_point.ssid)
                if not access_point.ssid and access_point.bssid:
                    name = f"{name} ({access_point.bssid})"
                label_bits = [name]
                if access_point.connected:
                    label_bits.append(self._label(30015, "Connected"))
                color = "green" if access_point.connected else "yellow"
                context_menu = []
                if access_point.connected:
                    context_menu.append(
                        (self._label(30044, "Disconnect"), "disconnect_wifi", {"service_id": access_point.service_id})
                    )
                context_menu.append(
                    (self._label(30045, "Forget network"), "forget_wifi", {"service_id": access_point.service_id})
                )
                items.append(
                    MenuItem(
                        "  ".join(label_bits),
                        "connect",
                        {"service_id": access_point.service_id},
                        False,
                        color,
                        tuple(context_menu),
                    )
                )

        self._render(self._label(30046, "Saved Wi-Fi profiles"), items)

    def toggle_interface(self) -> None:
        kind = self._param("kind")
        if not kind:
            self.show_interfaces()
            return

        snapshot = self._snapshot()
        interface = next((item for item in snapshot.interfaces if item.kind.value == kind), None)
        if interface is None:
            self._notify(self._label(30009, "Status"), f"Unknown interface kind: {kind}", level="error")
            self.show_interfaces()
            return

        try:
            self.backend.set_interface_enabled(interface.kind, not interface.enabled)
        except (BackendUnavailableError, Exception) as exc:
            self._notify(self._label(30009, "Status"), str(exc), level="error")

        self._refresh_container()
        self.show_interfaces()

    def toggle_tailscale(self) -> None:
        status = tailscale.get_status()
        if not status.installed:
            self._notify(self._label(30051, "Tailscale"), self._label(30061, "not installed"), level="error")
            self.show_interfaces()
            return

        try:
            result = self.backend.set_tailscale_enabled(not status.running)
        except (BackendUnavailableError, Exception) as exc:
            self._notify(self._label(30051, "Tailscale"), str(exc), level="error")
            self.show_interfaces()
            return

        if result.get("ok"):
            word = self._label(30059, "off") if status.running else self._label(30060, "on")
            self._notify(self._label(30051, "Tailscale"), word)
        else:
            message = str(result.get("output") or "") or self._label(30062, "Tailscale command failed")
            self._notify(self._label(30051, "Tailscale"), message, level="error")

        self._refresh_container()
        self.show_interfaces()

    def install_system_integration(self) -> None:
        dialog = xbmcgui.Dialog()
        confirmed = dialog.yesno(
            self._label(30033, "System integration"),
            self._label(30036, "Run the bootstrap now?"),
            self._label(30042, "The add-on will install the helper service and required system files."),
        )
        if not confirmed:
            self.show_root()
            return

        try:
            result = run_system_install(self.addon_path)
        except (OSError, ValueError) as exc:
            self._notify(self._label(30033, "System integration"), str(exc), level="error")
            self.show_root()
            return

        if result.ok:
            self._refresh_bootstrap_status()
            self._notify(
                self._label(30033, "System integration"),
                self._label(30038, "Bootstrap completed successfully"),
            )
            if result.output:
                self._show_text(self._label(30037, "Installer output"), result.output)
            self.reload_backend()
            return

        details = result.output or self._label(30040, "The installer returned an error")
        self._show_text(self._label(30037, "Installer output"), details)
        self.show_root()

    def _refresh_bootstrap_status(self) -> None:
        self.bootstrap_status = detect_bootstrap_status(self.addon_path)

    def _maybe_prompt_bootstrap(self) -> bool:
        self._refresh_bootstrap_status()
        if not self.bootstrap_status.needs_bootstrap:
            return False
        if self._bootstrap_prompt_shown:
            return False

        self._bootstrap_prompt_shown = True
        confirmed = xbmcgui.Dialog().yesno(
            self._label(30033, "System integration"),
            self._label(30041, "Bootstrap is required before network control will work"),
            self._label(30042, "The add-on will install the helper service and required system files."),
        )
        if not confirmed:
            return False

        self.install_system_integration()
        return True

    @staticmethod
    def _parse_int(value: str) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _parse_dns(value: str) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(part.strip() for part in value.split(",") if part.strip())

    @staticmethod
    def _needs_password(access_point) -> bool:
        if access_point is None:
            return True
        return bool(access_point.security) and not access_point.remembered

    @staticmethod
    def _state_word(enabled: bool) -> str:
        return "enabled" if enabled else "disabled"


def run(argv: list[str] | None = None) -> None:
    if xbmcaddon is None or xbmcgui is None or xbmcplugin is None:
        return
    NetworkAssistantApp(argv=argv).run()