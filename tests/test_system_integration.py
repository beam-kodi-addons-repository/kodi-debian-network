from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from resources.lib.system_integration import (
    DEFAULT_RUNTIME_DIR,
    build_system_install_command,
    detect_bootstrap_status,
    run_system_install,
)


class SystemIntegrationTests(unittest.TestCase):
    def test_build_system_install_command_with_sudo(self) -> None:
        self.assertEqual(
            build_system_install_command(
                "/opt/addons/plugin.program.networkassistant",
                kodi_user="kodi",
                use_sudo=True,
            ),
            [
                "sudo",
                "-n",
                "/opt/addons/plugin.program.networkassistant/package/install-system-integration.sh",
                "--addon-path",
                "/opt/addons/plugin.program.networkassistant",
                "--kodi-user",
                "kodi",
                "--install-sudoers",
            ],
        )

    def test_build_system_install_command_as_root(self) -> None:
        self.assertEqual(
            build_system_install_command(
                "/opt/addons/plugin.program.networkassistant",
                kodi_user="root",
                use_sudo=False,
            ),
            [
                "/opt/addons/plugin.program.networkassistant/package/install-system-integration.sh",
                "--addon-path",
                "/opt/addons/plugin.program.networkassistant",
                "--kodi-user",
                "root",
            ],
        )

    def test_detect_bootstrap_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            addon_path = os.path.join(temp_dir, "addon")
            runtime_dir = os.path.join(temp_dir, "runtime")
            service_path = os.path.join(temp_dir, "kodi-network-helper.service")
            os.makedirs(os.path.join(addon_path, "package"), exist_ok=True)
            os.makedirs(os.path.join(runtime_dir, "resources", "lib", "helper"), exist_ok=True)
            open(os.path.join(addon_path, "package", "install-system-integration.sh"), "w", encoding="utf-8").close()

            status = detect_bootstrap_status(addon_path, runtime_dir=runtime_dir, service_path=service_path)
            self.assertTrue(status.installer_script_exists)
            self.assertTrue(status.needs_bootstrap)

            open(os.path.join(runtime_dir, "resources", "lib", "helper", "server.py"), "w", encoding="utf-8").close()
            open(service_path, "w", encoding="utf-8").close()

            status = detect_bootstrap_status(addon_path, runtime_dir=runtime_dir, service_path=service_path)
            self.assertTrue(status.is_bootstrapped)

    def test_run_system_install(self) -> None:
        completed = subprocess.CompletedProcess(
            [
                "sudo",
                "-n",
                "/opt/addons/plugin.program.networkassistant/package/install-system-integration.sh",
                "--addon-path",
                "/opt/addons/plugin.program.networkassistant",
                "--kodi-user",
                "kodi",
                "--install-sudoers",
            ],
            0,
            stdout="ok\n",
            stderr="",
        )

        with mock.patch("resources.lib.system_integration.subprocess.run", return_value=completed) as run_mock:
            result = run_system_install("/opt/addons/plugin.program.networkassistant", kodi_user="kodi", use_sudo=True)

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "ok")
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()