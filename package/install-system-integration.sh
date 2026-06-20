#!/usr/bin/env bash

set -euo pipefail

ADDON_ID="plugin.program.networkassistant"
DEFAULT_ADDON_PATH="/usr/share/kodi/addons/${ADDON_ID}"
DEFAULT_RUNTIME_DIR="/usr/local/lib/kodi-network-assistant"
DEFAULT_INSTALLER_PATH="/usr/local/sbin/kodi-network-assistant-system-install"
DEFAULT_CONFIG_PATH="/etc/default/kodi-network-assistant"
DEFAULT_SERVICE_PATH="/etc/systemd/system/kodi-network-helper.service"
DEFAULT_SOCKET_PATH="/run/kodi-network-helper.sock"
DEFAULT_KODI_USER="kodi"

addon_path=""
runtime_dir=""
installer_path="${DEFAULT_INSTALLER_PATH}"
config_path="${DEFAULT_CONFIG_PATH}"
service_path="${DEFAULT_SERVICE_PATH}"
socket_path=""
kodi_user=""
install_sudoers="0"
skip_start="0"
script_source="$(readlink -f "$0")"

die() {
  echo "Error: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: install-system-integration.sh [options]

Options:
  --addon-path PATH       Path to the installed Kodi add-on.
  --kodi-user USER        Kodi runtime user. Default: kodi
  --runtime-dir PATH      Root-owned runtime copy. Default: /usr/local/lib/kodi-network-assistant
  --installer-path PATH   Root-owned installer command. Default: /usr/local/sbin/kodi-network-assistant-system-install
  --config-path PATH      Config file for repeated installs. Default: /etc/default/kodi-network-assistant
  --service-path PATH     systemd unit path. Default: /etc/systemd/system/kodi-network-helper.service
  --socket PATH           Helper socket path. Default: /run/kodi-network-helper.sock
  --install-sudoers       Install a narrow sudoers rule for the Kodi user.
  --skip-start            Do not restart the helper service after install.
  --help                  Show this help.

Running the installed root-owned command later requires no arguments:
  sudo -n /usr/local/sbin/kodi-network-assistant-system-install
EOF
}

load_config() {
  if [[ -f "${config_path}" ]]; then
    # shellcheck disable=SC1090
    source "${config_path}"
    addon_path="${addon_path:-${ADDON_PATH:-}}"
    runtime_dir="${runtime_dir:-${RUNTIME_DIR:-${DEFAULT_RUNTIME_DIR}}}"
    socket_path="${socket_path:-${HELPER_SOCKET:-${DEFAULT_SOCKET_PATH}}}"
    kodi_user="${kodi_user:-${KODI_USER:-${DEFAULT_KODI_USER}}}"
  fi
}

detect_addon_path() {
  if [[ -n "${addon_path}" ]]; then
    return
  fi

  if [[ -d "${DEFAULT_ADDON_PATH}" ]]; then
    addon_path="${DEFAULT_ADDON_PATH}"
    return
  fi

  if getent passwd "${kodi_user}" >/dev/null 2>&1; then
    local home_dir
    home_dir="$(getent passwd "${kodi_user}" | cut -d: -f6)"
    if [[ -n "${home_dir}" && -d "${home_dir}/.kodi/addons/${ADDON_ID}" ]]; then
      addon_path="${home_dir}/.kodi/addons/${ADDON_ID}"
      return
    fi
  fi

  if [[ -f "${PWD}/addon.xml" && -f "${PWD}/resources/lib/helper/server.py" ]]; then
    addon_path="${PWD}"
    return
  fi

  die "Unable to detect the installed add-on path. Pass --addon-path explicitly."
}

validate_environment() {
  [[ "$(id -u)" == "0" ]] || die "Run this installer as root."
  command -v python3 >/dev/null 2>&1 || die "python3 is required"
  command -v connmanctl >/dev/null 2>&1 || die "connmanctl is required"
  command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
  [[ -f "${addon_path}/addon.xml" ]] || die "addon.xml not found in ${addon_path}"
  [[ -f "${addon_path}/resources/lib/helper/server.py" ]] || die "helper server not found in ${addon_path}"
}

install_runtime_copy() {
  rm -rf "${runtime_dir}"
  install -d -m 0755 "${runtime_dir}"
  cp -a "${addon_path}/resources" "${runtime_dir}/"
}

install_self_copy() {
  install -D -m 0755 "${script_source}" "${installer_path}"
}

write_config() {
  install -d -m 0755 "$(dirname "${config_path}")"
  cat > "${config_path}" <<EOF
ADDON_PATH='${addon_path}'
RUNTIME_DIR='${runtime_dir}'
HELPER_SOCKET='${socket_path}'
KODI_USER='${kodi_user}'
EOF
  chmod 0644 "${config_path}"
}

write_service() {
  install -d -m 0755 "$(dirname "${service_path}")"
  cat > "${service_path}" <<EOF
[Unit]
Description=Kodi Network Assistant helper service
Wants=connman.service
After=connman.service dbus.service network.target
ConditionPathExists=/usr/bin/connmanctl

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/bin/python3 ${runtime_dir}/resources/lib/helper/server.py --socket ${socket_path} --backend connman
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "${service_path}"
}

install_sudoers_rule() {
  local sudoers_path="/etc/sudoers.d/kodi-network-assistant"
  cat > "${sudoers_path}" <<EOF
${kodi_user} ALL=(root) NOPASSWD: ${installer_path}
EOF
  chmod 0440 "${sudoers_path}"
  visudo -cf "${sudoers_path}" >/dev/null
}

restart_service() {
  local service_name
  service_name="$(basename "${service_path}")"
  systemctl daemon-reload
  systemctl enable "${service_name}" >/dev/null
  if [[ "${skip_start}" == "0" ]]; then
    systemctl restart "${service_name}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --addon-path)
      addon_path="$2"
      shift 2
      ;;
    --kodi-user)
      kodi_user="$2"
      shift 2
      ;;
    --runtime-dir)
      runtime_dir="$2"
      shift 2
      ;;
    --installer-path)
      installer_path="$2"
      shift 2
      ;;
    --config-path)
      config_path="$2"
      shift 2
      ;;
    --service-path)
      service_path="$2"
      shift 2
      ;;
    --socket)
      socket_path="$2"
      shift 2
      ;;
    --install-sudoers)
      install_sudoers="1"
      shift
      ;;
    --skip-start)
      skip_start="1"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

load_config
runtime_dir="${runtime_dir:-${DEFAULT_RUNTIME_DIR}}"
socket_path="${socket_path:-${DEFAULT_SOCKET_PATH}}"
kodi_user="${kodi_user:-${DEFAULT_KODI_USER}}"
detect_addon_path
validate_environment
install_runtime_copy
install_self_copy
write_config
write_service
if [[ "${install_sudoers}" == "1" ]]; then
  install_sudoers_rule
fi
restart_service

cat <<EOF
System integration installed.
- Add-on path: ${addon_path}
- Runtime copy: ${runtime_dir}
- Installer command: ${installer_path}
- Service unit: ${service_path}
- Helper socket: ${socket_path}
EOF