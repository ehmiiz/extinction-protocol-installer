import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/ehmiiz/extinction-protocol-installer.git"


@unittest.skipIf(os.geteuid() == 0, "Bootstrap deliberately refuses to run as root")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "local checkout"
        self.checkout.mkdir()
        self.script = (ROOT / "install.sh").read_text()
        (self.checkout / "install.sh").write_text(self.script)
        (self.checkout / "install.py").write_text("# Test installer entry point\n")
        (self.checkout / "requirements.txt").write_text("rich>=13.7,<15\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        self.data = self.root / "data directory"
        self.state = self.data / "extinction-protocol-installer"
        self.managed = self.state / "source"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "XDG_DATA_HOME": str(self.data),
            "BOOTSTRAP_TEST_LOG": str(self.log),
            "BOOTSTRAP_TEST_SOURCE": str(self.checkout),
        }
        self.write_command("python3", """
            import json
            import os
            from pathlib import Path
            import shutil
            import sys

            args = sys.argv[1:]
            with open(os.environ["BOOTSTRAP_TEST_LOG"], "a") as log:
                log.write(json.dumps([Path(sys.argv[0]).name, *args]) + "\\n")
            if args[0] == "-c":
                sys.exit(int(os.environ.get("TEST_OLD_PYTHON", "0")))
            if args[:2] == ["-m", "venv"]:
                target = Path(args[2]) / "bin" / "python"
                target.parent.mkdir(parents=True)
                shutil.copy2(__file__, target)
            elif args[:2] == ["-m", "pip"]:
                if os.environ.get("TEST_PIP_ERROR"):
                    print("Simulated dependency download failure", file=sys.stderr)
                    sys.exit(1)
            else:
                sys.exit(int(os.environ.get("TEST_INSTALL_EXIT", "0")))
        """)
        self.write_command("git", f"""
            import json
            import os
            from pathlib import Path
            import shutil
            import sys

            args = sys.argv[1:]
            with open(os.environ["BOOTSTRAP_TEST_LOG"], "a") as log:
                log.write(json.dumps(["git", *args]) + "\\n")
            if args[0] == "clone":
                if os.environ.get("TEST_GIT_ERROR"):
                    print("Simulated clone failure", file=sys.stderr)
                    sys.exit(1)
                target = Path(args[-1])
                shutil.copytree(os.environ["BOOTSTRAP_TEST_SOURCE"], target)
                (target / ".git").mkdir()
            elif args[2:5] == ["remote", "get-url", "origin"]:
                print(os.environ.get("TEST_ORIGIN", {REPOSITORY_URL!r}))
            elif args[2] == "diff":
                sys.exit(int(os.environ.get("TEST_DIRTY_CHECKOUT", "0")))
            elif args[2] == "pull":
                sys.exit(int(os.environ.get("TEST_GIT_ERROR", "0")))
            else:
                raise AssertionError(args)
        """)

    def write_command(self, name, body):
        command = self.bin / name
        command.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
        command.chmod(0o755)

    def run_bootstrap(self, *args, remote=False, extra_env=None):
        command = ["bash", "-s", "--"] if remote else ["bash", str(self.checkout / "install.sh")]
        return subprocess.run(
            [*command, *args],
            input=self.script if remote else None,
            cwd=self.root,
            env={**self.env, **(extra_env or {})},
            capture_output=True, text=True, timeout=15,
        )

    def commands(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def installer_commands(self):
        return [command for command in self.commands() if command[1].endswith("install.py")]

    def test_local_setup_is_one_command_and_preserves_arguments(self):
        destination = str(self.root / "Game Library $literal")
        result = self.run_bootstrap("--install-dir", destination, "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.checkout / ".venv/bin/python").is_file())
        self.assertEqual(self.installer_commands(), [[
            "python", str(self.checkout / "install.py"), "--install-dir", destination, "--force",
        ]])
        self.assertFalse(any(command[0] == "git" for command in self.commands()))
        pip = [command for command in self.commands() if command[1:3] == ["-m", "pip"]]
        self.assertEqual(len(pip), 1)
        self.assertEqual(pip[0][-1], str(self.checkout / "requirements.txt"))

    def test_local_rerun_reuses_environment(self):
        for _ in range(2):
            result = self.run_bootstrap("--check")
            self.assertEqual(result.returncode, 0, result.stderr)
        creations = [command for command in self.commands() if command[1:3] == ["-m", "venv"]]
        self.assertEqual(len(creations), 1)
        self.assertEqual(len(self.installer_commands()), 2)

    def test_piped_script_downloads_managed_checkout_then_updates_it(self):
        for _ in range(2):
            result = self.run_bootstrap("--check", remote=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        clones = [command for command in self.commands() if command[:2] == ["git", "clone"]]
        pulls = [command for command in self.commands() if command[0] == "git" and "pull" in command]
        self.assertEqual(len(clones), 1)
        self.assertEqual(clones[0][-2:], [REPOSITORY_URL, str(self.managed)])
        self.assertEqual(len(pulls), 1)
        self.assertIn("--ff-only", pulls[0])
        self.assertTrue((self.managed / ".venv/bin/python").is_file())
        self.assertEqual(self.installer_commands()[0], [
            "python", str(self.managed / "install.py"), "--check",
        ])

    def test_downloaded_standalone_script_also_fetches_checkout(self):
        standalone = self.root / "downloaded-install.sh"
        standalone.write_text(self.script)
        result = subprocess.run(
            ["bash", str(standalone), "--check"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.managed / ".venv/bin/python").exists())

    def test_dependency_failure_is_visible_and_does_not_start_installer(self):
        result = self.run_bootstrap(extra_env={"TEST_PIP_ERROR": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Simulated dependency download failure", result.stderr)
        self.assertIn("Could not install Python dependencies", result.stderr)
        self.assertEqual(self.installer_commands(), [])

    def test_clone_failure_does_not_start_installer(self):
        result = self.run_bootstrap(remote=True, extra_env={"TEST_GIT_ERROR": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not download the installer", result.stderr)
        self.assertEqual(self.installer_commands(), [])

    def test_managed_checkout_with_local_edits_is_not_updated(self):
        self.assertEqual(self.run_bootstrap(remote=True).returncode, 0)
        self.log.unlink()
        result = self.run_bootstrap(remote=True, extra_env={"TEST_DIRTY_CHECKOUT": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preserving them", result.stderr)
        self.assertFalse(any("pull" in command for command in self.commands()))
        self.assertEqual(self.installer_commands(), [])

    def test_unexpected_managed_repository_is_preserved(self):
        self.assertEqual(self.run_bootstrap(remote=True).returncode, 0)
        self.log.unlink()
        result = self.run_bootstrap(remote=True, extra_env={"TEST_ORIGIN": "unrelated-repo"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected repository", result.stderr)
        self.assertEqual(self.installer_commands(), [])

    def test_old_python_fails_before_creating_environment(self):
        result = self.run_bootstrap(extra_env={"TEST_OLD_PYTHON": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.12 or newer", result.stderr)
        self.assertFalse(self.state.exists())

    def test_relative_xdg_directory_is_rejected(self):
        result = self.run_bootstrap(extra_env={"XDG_DATA_HOME": "relative"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("XDG_DATA_HOME must be an absolute path", result.stderr)

    def test_concurrent_bootstrap_is_rejected(self):
        self.state.mkdir(parents=True)
        with (self.state / "bootstrap.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another installer is already running", result.stderr)
        self.assertEqual(self.installer_commands(), [])

    def test_installer_exit_status_is_preserved(self):
        result = self.run_bootstrap(extra_env={"TEST_INSTALL_EXIT": "7"})
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
