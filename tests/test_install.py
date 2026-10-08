import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import install


ELF = b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\x00"
PNG = b"\x89PNG\r\n\x1a\n"
CHECKSUM = "a" * 64
ASSET = {
    "name": "Extinction-Protocol-Linux-0.14.1-UNVERIFIED.tar.gz",
    "browser_download_url": "https://example.invalid/game.tar.gz",
    "digest": "sha256:" + CHECKSUM,
}
RELEASE = {"tag_name": "v0.14.1", "assets": [ASSET]}


def make_archive(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "/tmp/outside"
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
    return path


class AssetTests(unittest.TestCase):
    def test_case_and_separator_variations(self):
        for name in (
            ASSET["name"], "extinction-protocol-linux-v2.tar.gz",
            "extinctionProtocolLinux-2.tar.gz", "Extinction_Protocol_LINUX.tar.gz",
        ):
            with self.subTest(name=name):
                asset = {**ASSET, "name": name}
                self.assertEqual(install.select_game_asset([asset]), asset)

    def test_ignores_other_platforms_architectures_and_formats(self):
        unrelated = [
            {"name": name} for name in (
                "Extinction-Protocol-Windows.zip",
                "Extinction-Protocol-Linux-arm64.tar.gz",
                "Extinction-Protocol-Linux-aarch64.tar.gz",
                "Extinction-Protocol-Linux-i686.tar.gz",
                "Extinction-Protocol-Linux.tar.gz.sha256",
                "Another-Game-Linux.tar.gz",
            )
        ]
        self.assertEqual(install.select_game_asset(unrelated + [ASSET]), ASSET)

    def test_missing_or_ambiguous_assets_fail(self):
        for assets in ([], [ASSET, {**ASSET, "name": "extinctionProtocolLinux-2.tar.gz"}]):
            with self.subTest(assets=assets), self.assertRaises(install.InstallError):
                install.select_game_asset(assets)

    def test_checksum_from_metadata(self):
        self.assertEqual(install.asset_checksum(ASSET), CHECKSUM)

    def test_checksum_falls_back_to_release_file(self):
        asset = {**ASSET, "digest": None}
        sums = {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.invalid/sums"}
        for separator in ("  ", " *"):
            with patch("install.request", return_value=io.BytesIO(
                f"{CHECKSUM}{separator}{asset['name']}\n".encode()
            )):
                self.assertEqual(install.asset_checksum(asset, {"assets": [sums]}), CHECKSUM)

    def test_missing_checksum_fails_closed(self):
        with self.assertRaises(install.InstallError):
            install.asset_checksum({**ASSET, "digest": None})

    def test_token_only_sent_to_github_api(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}), patch("install.urlopen") as open_url:
            install.request("https://api.github.com/repos/example/repo/releases/latest")
            self.assertEqual(open_url.call_args.args[0].get_header("Authorization"), "Bearer test-token")
            for url in (
                "https://github.com/example/repo/releases/download/v1/file",
                "https://raw.githubusercontent.com/example/repo/v1/logo.png",
                "https://api.github.com.example.invalid/",
            ):
                install.request(url)
                self.assertIsNone(open_url.call_args.args[0].get_header("Authorization"))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, entries=None):
        return make_archive(
            self.root / "game.tar.gz",
            entries if entries is not None else [
                ("game/Extinction Protocol.x86_64", ELF, tarfile.REGTYPE),
                ("game/Extinction Protocol.pck", b"game data", tarfile.REGTYPE),
            ],
        )

    def test_extracts_executable_and_companion_files(self):
        destination = self.root / "game"
        executable = install.extract_game(self.archive(), destination)
        self.assertEqual(executable.read_bytes(), ELF)
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertEqual(executable.with_suffix(".pck").read_bytes(), b"game data")

    def test_rejects_unsafe_archive_entries_before_extraction(self):
        for name, kind in (
            ("../outside", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
            ("link", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
            ("pipe", tarfile.FIFOTYPE), ("device", tarfile.CHRTYPE),
            ("game/_sc_", tarfile.REGTYPE), ("game/._sc_", tarfile.REGTYPE),
        ):
            with self.subTest(name=name):
                destination = self.root / "extracted"
                archive = self.archive([("safe", b"safe", tarfile.REGTYPE), (name, b"", kind)])
                with self.assertRaises(install.InstallError):
                    install.extract_game(archive, destination)
                self.assertEqual(list(destination.iterdir()), [])
                destination.rmdir()

    def test_rejects_wrong_architecture_and_missing_or_multiple_executables(self):
        for entries in (
            [],
            [("Extinction Protocol", ELF[:18] + b"\xb7\x00", tarfile.REGTYPE)],
            [
                ("Extinction Protocol", ELF, tarfile.REGTYPE),
                ("Extinction Protocol debug", ELF, tarfile.REGTYPE),
            ],
        ):
            with self.subTest(entries=entries):
                with self.assertRaises(install.InstallError):
                    install.extract_game(self.archive(entries), self.root / "extracted")
                shutil.rmtree(self.root / "extracted")

    def test_launcher_preserves_arguments_working_directory_and_environment(self):
        logo = self.root / "logo.png"
        logo.write_bytes(PNG)
        appdir = self.root / "App Dir's $odd %name"
        archive = self.archive([("nested dir/Extinction Protocol.x86_64", ELF, tarfile.REGTYPE)])
        install.prepare_appdir(archive, logo, appdir)
        executable = appdir / "usr/lib/extinction-protocol/nested dir/Extinction Protocol.x86_64"
        executable.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$HOME\" \"$XDG_DATA_HOME\" \"$@\"\n"
        )
        env = {**os.environ, "XDG_DATA_HOME": str(self.root / "original data")}
        env.pop("APPDIR", None)
        args = ["--headless", "argument with spaces", "$literal", "semi;colon"]
        result = subprocess.run(
            [str(appdir / "AppRun"), *args], env=env, capture_output=True, text=True, check=True
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [str(executable.parent), env["HOME"], env["XDG_DATA_HOME"], "--fullscreen", *args],
        )
        self.assertEqual((appdir / ".DirIcon").read_bytes(), PNG)
        self.assertIn("Name=Extinction Protocol\n", (appdir / "extinction-protocol.desktop").read_text())

    def test_verified_download_and_cache_hit(self):
        data = b"downloaded bytes"
        checksum = hashlib.sha256(data).hexdigest()
        destination = self.root / "cache" / checksum
        with patch("install.request", return_value=io.BytesIO(data)) as request:
            install.download("https://example.invalid/asset", destination, checksum)
            install.download("https://example.invalid/asset", destination, checksum)
            request.assert_called_once()
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_corrupt_cache_is_redownloaded(self):
        destination = self.root / "download"
        destination.write_bytes(b"corrupt")
        with patch("install.request", return_value=io.BytesIO(b"correct")):
            install.download(
                "https://example.invalid/asset", destination, hashlib.sha256(b"correct").hexdigest()
            )
        self.assertEqual(destination.read_bytes(), b"correct")

    def test_checksum_mismatch_and_network_errors_leave_no_partial_file(self):
        destination = self.root / "download"
        with patch("install.request", return_value=io.BytesIO(b"bad")):
            with self.assertRaises(install.InstallError):
                install.download("https://example.invalid/asset", destination, CHECKSUM)
        with patch("install.request", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                install.download("https://example.invalid/asset", destination, CHECKSUM)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_desktop_exec_escaping(self):
        self.assertEqual(install.desktop_exec('/home/a b/game'), '"/home/a b/game"')
        self.assertEqual(install.desktop_exec('/a%/b'), '"/a%%/b"')
        self.assertEqual(install.desktop_exec('/a"b'), '"/a\\\\"b"')
        self.assertEqual(install.desktop_exec('/a$b'), '"/a\\\\$b"')
        self.assertEqual(install.desktop_value("a\\b\nc"), "a\\\\b\\nc")

    def test_atomic_write_failure_keeps_previous_file(self):
        destination = self.root / "existing"
        destination.write_bytes(b"old")
        with patch("pathlib.Path.replace", side_effect=OSError("cannot replace")):
            with self.assertRaises(OSError):
                install.atomic_write(destination, b"new")
        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(list(self.root.iterdir()), [destination])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        self.applications = self.root / 'Applications with spaces $and %quotes"'
        self.args = argparse.Namespace(install_dir=self.applications, check=False, force=False)
        self.logo = self.root / "logo.png"
        self.logo.write_bytes(PNG)
        self.save = self.data / "godot/app_userdata/Extinction Protocol/progress.save"
        self.save.parent.mkdir(parents=True)
        self.save.write_bytes(b"irreplaceable player progress")
        self.save_stat = self.save.stat()
        patches = [
            patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data), "XDG_CACHE_HOME": str(self.cache)}),
            patch("install.os.geteuid", return_value=1000),
            patch("install.platform.system", return_value="Linux"),
            patch("install.platform.machine", return_value="x86_64"),
            patch("install.latest_release", return_value=RELEASE),
            patch("install.download", return_value=self.logo),
            patch("install.download_asset", return_value=self.root / "archive"),
            patch("install.shutil.which", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.builder = patch("install.build_appimage", side_effect=self.build)
        self.build_mock = self.builder.start()
        self.addCleanup(self.builder.stop)

    def build(self, archive, logo, work, cache):
        image = work / install.IMAGE_NAME
        image.write_bytes(b"packaged game")
        image.chmod(0o755)
        return image

    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            install.install(self.args)

    def assert_save_untouched(self):
        self.assertEqual(self.save.read_bytes(), b"irreplaceable player progress")
        self.assertEqual(self.save.stat().st_mtime_ns, self.save_stat.st_mtime_ns)
        self.assertEqual(self.save.stat().st_ino, self.save_stat.st_ino)

    def test_install_update_noop_and_force_preserve_saves(self):
        self.run_install()
        image = self.applications / install.IMAGE_NAME
        self.assertEqual(image.read_bytes(), b"packaged game")
        self.assertTrue(os.access(image, os.X_OK))
        self.run_install()
        self.assertEqual(self.build_mock.call_count, 1)
        newer = {"tag_name": "v0.15", "assets": [{**ASSET, "digest": "sha256:" + "b" * 64}]}
        with patch("install.latest_release", return_value=newer):
            self.run_install()
        self.assertEqual(self.build_mock.call_count, 2)
        state = json.loads((self.applications / ".extinction-protocol.json").read_text())
        self.assertEqual(state["tag"], "v0.15")
        self.args.force = True
        with patch("install.latest_release", return_value=newer):
            self.run_install()
        self.assertEqual(self.build_mock.call_count, 3)
        self.assert_save_untouched()

    def test_same_tag_replaced_asset_triggers_rebuild(self):
        self.run_install()
        changed = {**RELEASE, "assets": [{**ASSET, "digest": "sha256:" + "b" * 64}]}
        with patch("install.latest_release", return_value=changed):
            self.run_install()
        self.assertEqual(self.build_mock.call_count, 2)

    def test_noop_restores_missing_desktop_entry_and_icon(self):
        self.run_install()
        desktop = self.data / "applications/extinction-protocol.desktop"
        icon = self.data / "icons/extinction-protocol.png"
        desktop.unlink()
        icon.unlink()
        self.run_install()
        self.assertEqual(self.build_mock.call_count, 1)
        self.assertIn(install.desktop_exec(self.applications / install.IMAGE_NAME), desktop.read_text())
        self.assertEqual(icon.read_bytes(), PNG)

    def test_failed_update_keeps_previous_game_and_metadata(self):
        self.run_install()
        state = (self.applications / ".extinction-protocol.json").read_bytes()
        self.args.force = True
        self.build_mock.side_effect = subprocess.CalledProcessError(1, "appimagetool")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_install()
        self.assertEqual((self.applications / install.IMAGE_NAME).read_bytes(), b"packaged game")
        self.assertEqual((self.applications / ".extinction-protocol.json").read_bytes(), state)
        self.assertEqual(len(list(self.applications.iterdir())), 3)
        self.assert_save_untouched()

    def test_failed_download_keeps_previous_game(self):
        self.run_install()
        self.args.force = True
        with patch("install.download_asset", side_effect=install.InstallError("SHA-256 mismatch")):
            with self.assertRaisesRegex(install.InstallError, "SHA-256 mismatch"):
                self.run_install()
        self.assertEqual((self.applications / install.IMAGE_NAME).read_bytes(), b"packaged game")
        self.assertEqual(self.build_mock.call_count, 1)
        self.assert_save_untouched()

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "desktop validator not installed")
    def test_desktop_entry_with_special_characters_is_valid(self):
        self.run_install()
        subprocess.run(
            ["desktop-file-validate", str(self.data / "applications/extinction-protocol.desktop")],
            check=True, capture_output=True, text=True,
        )

    def test_check_makes_no_installation_changes(self):
        self.args.check = True
        self.run_install()
        self.assertFalse(self.applications.exists())
        self.assertFalse(self.cache.exists())
        self.build_mock.assert_not_called()
        self.assert_save_untouched()

    def test_rejects_root_unsupported_platform_and_relative_xdg(self):
        for item in (
            patch("install.os.geteuid", return_value=0),
            patch("install.platform.machine", return_value="aarch64"),
            patch("install.platform.system", return_value="Darwin"),
            patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}),
        ):
            with item, self.assertRaises(install.InstallError):
                self.run_install()
        self.assertFalse(self.applications.exists())

    def test_concurrent_install_fails_explicitly(self):
        self.applications.mkdir()
        with (self.applications / ".extinction-protocol.lock").open("a") as lock:
            install.fcntl.flock(lock, install.fcntl.LOCK_EX | install.fcntl.LOCK_NB)
            with self.assertRaisesRegex(install.InstallError, "Another installer"):
                self.run_install()

    def test_invalid_state_is_reported(self):
        self.run_install()
        (self.applications / ".extinction-protocol.json").write_text("invalid")
        with self.assertRaisesRegex(install.InstallError, "Invalid install metadata"):
            self.run_install()

    def test_main_reports_error_with_nonzero_exit(self):
        with patch("install.install", side_effect=install.InstallError("specific failure")):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(install.main([]), 1)
            self.assertIn("specific failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
