# Network Assistant for Kodi

Kodi add-on for managing Wi-Fi and Ethernet on a Raspberry Pi 5 running Debian.

This project is meant to be delivered through a Kodi repository. The add-on now contains its own bootstrap flow and can install the required system integration itself, as long as Kodi runs either as `root` or as a user that can execute `sudo -n` without a password.

## What The Add-on Does

- Lists nearby Wi-Fi networks.
- Connects and disconnects Wi-Fi.
- Enables and disables Wi-Fi and Ethernet.
- Stores Wi-Fi credentials in NetworkManager-managed profiles.
- Supports autoconnect.
- Supports DHCP and static IPv4, plus custom DNS.

## Runtime Model

- Kodi runs the user interface.
- A root systemd helper service performs privileged network operations.
- The helper talks to NetworkManager via `nmcli`.
- NetworkManager is a required runtime dependency, not an optional one. It is Debian's default network manager, so it is already present on a stock Debian image.
- The production helper runs from a root-owned runtime copy under `/usr/local/lib/kodi-network-assistant/`, not directly from Kodi's add-on directory.
- The first add-on launch can offer a bootstrap action automatically if the system integration is not installed yet.

## What Is In The Repository

- Kodi add-on code.
- Helper service source.
- A systemd unit example.
- Shared Python models and protocol code.
- Unit tests and a demo backend for development.

## What Must Be Installed On The Device

Before the add-on can be used, the target Debian image must have:

- Kodi.
- NetworkManager.
- Python 3.
- systemd.
- Either Kodi running as `root`, or the Kodi user allowed to run `sudo -n` without a password.

If another network manager (ConnMan, `systemd-networkd`, `dhcpcd`, ...) is active, disable it. The add-on expects NetworkManager to own the network configuration it manages.

## Deployment Layout

The Kodi repository should install the add-on into Kodi’s normal add-on directory. On Debian-based Kodi installs this is typically:

- `/usr/share/kodi/addons/plugin.program.networkassistant/` for system-wide installs, or
- `~/.kodi/addons/plugin.program.networkassistant/` for user-local installs.

The helper service is separate from the Kodi repository payload. It should be installed as a system service, for example to:

- `/etc/systemd/system/kodi-network-helper.service`

The installer copies the helper runtime to:

- `/usr/local/lib/kodi-network-assistant/`

The root-owned bootstrap command is:

- `/usr/local/sbin/kodi-network-assistant-system-install`

## Recommended Install Steps

1. Make sure NetworkManager is installed and enabled on the device (it is the default on Debian).
2. Install the Kodi repository that contains this add-on.
3. Let Kodi install the add-on into its standard add-on directory.
4. Start the add-on in Kodi.
5. On first start, if bootstrap is missing, the add-on will offer to perform it.
6. Confirm the bootstrap action.
7. After bootstrap completes, reload or restart Kodi if needed.

Example host preparation for a system-wide setup:

```bash
sudo apt install network-manager python3
sudo systemctl enable --now NetworkManager
```

If Kodi runs as an unprivileged user, allow that user to execute passwordless sudo. The simplest model for a dedicated Kodi box is either:

- run Kodi as `root`, or
- place the Kodi user in a passwordless sudo rule.

Example narrow sudoers rule:

```bash
echo 'kodi ALL=(root) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/kodi-full
sudo chmod 0440 /etc/sudoers.d/kodi-full
```

If you want a stricter setup later, you can replace that broad rule with the narrow rule installed by the bootstrap itself.

The bootstrap will:

- verify `python3`, `nmcli`, and `systemctl`,
- run the packaged installer script from the add-on directory,
- copy the runtime files into `/usr/local/lib/kodi-network-assistant/`,
- install a root-owned installer command at `/usr/local/sbin/kodi-network-assistant-system-install`,
- write `/etc/default/kodi-network-assistant`,
- install `/etc/systemd/system/kodi-network-helper.service`,
- enable and restart the helper service,
- if bootstrap ran through `sudo`, install `/etc/sudoers.d/kodi-network-assistant` for future narrow repairs.

## Can The Add-on Install This Itself?

Yes. That is now the intended setup for a dedicated Kodi device.

What is implemented now:

- On first launch the add-on can detect that bootstrap is missing.
- The add-on can offer a bootstrap action immediately.
- The bootstrap runs the packaged script from the installed add-on directory.
- If Kodi already runs as `root`, the add-on runs the script directly.
- If Kodi runs as another user, the add-on runs it through `sudo -n`.

What is intentionally not recommended:

- Using unrestricted `sudo` permanently if you do not have to.
- Allowing the add-on to run arbitrary commands as root beyond the bootstrap path.

If you grant unrestricted `sudo` to the entire add-on, then any add-on bug or repository compromise becomes a root compromise of the whole box. For a dedicated Kodi appliance that may be an acceptable tradeoff, but it should still be a deliberate decision.

If your Debian image uses another manager such as ConnMan, `systemd-networkd`, or `dhcpcd`, make sure only one manager owns the interfaces that NetworkManager should control.

## Testing And Recovery Notes

Network changes can drop SSH instantly. For real-device testing, use one of these recovery options:

- HDMI plus keyboard.
- A serial console.
- A second independent management path, for example Ethernet while testing Wi-Fi.

Do not rely on SSH alone for the first Wi-Fi connection test. If the connection fails, the device may become unreachable until you fix the network locally.

## Current Status

- Kodi plugin shell with menu routing is in place.
- Kodi can detect missing bootstrap and offer to install the system integration on first launch.
- Shared network data model is in place.
- JSON-over-Unix-socket helper protocol is in place.
- Demo backend exists for UI development and tests.
- NetworkManager backend can read device/connection state and visible Wi-Fi networks, toggle Wi-Fi/Ethernet, and apply DHCP/static IP, DNS, and autoconnect settings to known connections.
- First-time Wi-Fi connections (including hidden networks) are made directly via `nmcli device wifi connect`, which accepts the passphrase as an argument.

## Development Commands

Run the tests with:

```bash
python3 -m unittest discover -s tests
```

Run a syntax check with:

```bash
python3 -m compileall main.py resources tests
```

Validate the installer script syntax with:

```bash
bash -n package/install-system-integration.sh
```

## Next Implementation Step

- Validate the `nmcli` terse-output parsing against a real NetworkManager install (the unit tests only cover captured/mocked output).