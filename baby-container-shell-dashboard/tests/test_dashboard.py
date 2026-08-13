import importlib.util
import os
import pathlib
import subprocess
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dashboard", ROOT / "dashboard.py")
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


class DashboardTest(unittest.TestCase):
    def test_create_payload_is_fixed_to_ssh_slot_and_required_capabilities(self):
        self.assertEqual(
            DASHBOARD.baby_create_payload(),
            {
                "slot": "ssh-shell",
                "instance_id": "ssh-shell-1",
                "cap_add": ["CHOWN", "SETUID", "SETGID", "SYS_CHROOT"],
            },
        )

    def test_ssh_rejects_unknown_user(self):
        with self.assertRaisesRegex(ValueError, "tester or root"):
            DASHBOARD.run_ssh_command("nobody", "id")

    def test_ssh_uses_password_environment_without_putting_password_in_argv(self):
        completed = subprocess.CompletedProcess([], 0, "ok\n", "")
        with mock.patch.object(DASHBOARD.subprocess, "run", return_value=completed) as run:
            result = DASHBOARD.run_ssh_command("tester", "id")
        argv = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("atakit-test", argv)
        self.assertEqual(environment["SSHPASS"], "atakit-test")
        self.assertEqual(result["exit_code"], 0)

    def test_fixed_checks_cover_user_switch_and_read_only_root_filesystem(self):
        self.assertIn("sudo -n id -u", DASHBOARD.NORMAL_USER_CHECKS)
        self.assertIn("/etc/shadow", DASHBOARD.NORMAL_USER_CHECKS)
        self.assertIn("su -s /bin/bash tester", DASHBOARD.ROOT_USER_CHECKS)
        self.assertIn("chown tester:tester", DASHBOARD.ROOT_USER_CHECKS)
        self.assertIn("ip link add baby-test", DASHBOARD.ROOT_USER_CHECKS)
        self.assertIn("socket.SOCK_RAW", DASHBOARD.ROOT_USER_CHECKS)
        self.assertIn("/etc/root-write", DASHBOARD.ROOT_USER_CHECKS)

    def test_baby_image_allows_only_root_to_read_password_hashes(self):
        containerfile = (ROOT / "baby-shell" / "Containerfile").read_text()
        self.assertIn("chmod 0400 /etc/shadow /etc/gshadow", containerfile)

    def test_sshd_allows_pseudo_terminals(self):
        sshd_config = (ROOT / "baby-shell" / "sshd_config").read_text()
        self.assertIn("PermitTTY yes", sshd_config)

    def test_workload_opts_in_to_portal_ip_environment(self):
        workload = (ROOT / "atakit-workload.toml").read_text()
        self.assertIn("ip-env = true", workload)

    def test_dashboard_prints_complete_public_ip_ssh_commands(self):
        page = DASHBOARD.render_index_html("203.0.113.10")
        self.assertIn(
            "ssh -t -p 2222 tester@203.0.113.10 bash -i",
            page,
        )
        self.assertIn(
            "ssh -t -p 2222 root@203.0.113.10 bash -i",
            page,
        )
        self.assertNotIn("__ATAKIT_PUBLIC_IP__", page)


if __name__ == "__main__":
    unittest.main()
