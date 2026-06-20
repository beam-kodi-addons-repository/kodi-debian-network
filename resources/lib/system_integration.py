from __future__ import annotations

import os
import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RUNTIME_DIR = "/usr/local/lib/kodi-network-assistant"
DEFAULT_SERVICE_PATH = "/etc/systemd/system/kodi-network-helper.service"


@dataclass(frozen=True)
class BootstrapStatus:
    installer_script_exists: bool
    runtime_helper_exists: bool
    service_exists: bool

    @property
    def is_bootstrapped(self) -> bool:
        return self.runtime_helper_exists and self.service_exists

    @property
    def needs_bootstrap(self) -> bool:
        return not self.is_bootstrapped


@dataclass(frozen=True)
class SystemInstallResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part).strip()


def get_current_user_name() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def build_bootstrap_installer_path(addon_path: str) -> str:
    if not addon_path:
        raise ValueError("Add-on path must not be empty")
    return str(Path(addon_path) / "package" / "install-system-integration.sh")


def detect_bootstrap_status(
    addon_path: str,
    runtime_dir: str = DEFAULT_RUNTIME_DIR,
    service_path: str = DEFAULT_SERVICE_PATH,
) -> BootstrapStatus:
    installer_script = Path(build_bootstrap_installer_path(addon_path))
    runtime_helper = Path(runtime_dir) / "resources" / "lib" / "helper" / "server.py"
    service_file = Path(service_path)
    return BootstrapStatus(
        installer_script_exists=installer_script.exists(),
        runtime_helper_exists=runtime_helper.exists(),
        service_exists=service_file.exists(),
    )


def build_system_install_command(
    addon_path: str,
    kodi_user: str | None = None,
    use_sudo: bool | None = None,
) -> list[str]:
    installer_script = build_bootstrap_installer_path(addon_path)
    user_name = kodi_user or get_current_user_name()
    if use_sudo is None:
        use_sudo = os.geteuid() != 0

    command: list[str] = []
    if use_sudo:
        command.extend(["sudo", "-n"])
    command.extend([
        installer_script,
        "--addon-path",
        addon_path,
        "--kodi-user",
        user_name,
    ])
    if use_sudo and user_name != "root":
        command.append("--install-sudoers")
    return command


def run_system_install(
    addon_path: str,
    kodi_user: str | None = None,
    use_sudo: bool | None = None,
) -> SystemInstallResult:
    completed = subprocess.run(
        build_system_install_command(addon_path, kodi_user=kodi_user, use_sudo=use_sudo),
        capture_output=True,
        text=True,
        check=False,
    )
    return SystemInstallResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )