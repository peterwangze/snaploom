import plistlib
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from snaploom.update_install_macos import _atomic_swap, install_macos, mac_install_target


def make_bundle(path, version, *, identifier="io.github.peterwangze.snaploom", cpu=0x0100000C):
    executable = path / "Contents" / "MacOS" / "Snaploom"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\xcf\xfa\xed\xfe" + cpu.to_bytes(4, "little") + version.encode("ascii"))
    executable.chmod(0o755)
    executable.parents[1].joinpath("Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identifier,
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "Snaploom",
        "CFBundleVersion": version,
        "CFBundleShortVersionString": version,
    }))
    return executable


class MacInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.target = self.root / "My Snaploom.app"
        self.executable = make_bundle(self.target, "1.0.21")
        self.mounted = self.root / "mounted"
        self.replacement = self.mounted / "Snaploom.app"
        self.dmg = self.root / "update.dmg"
        self.dmg.touch()

    def test_only_writable_snaploom_bundle_has_an_install_target(self):
        other = make_bundle(self.root / "Other.app", "1.0.21", identifier="test.other")
        translocated = make_bundle(self.root / "AppTranslocation" / "Snaploom.app", "1.0.21")
        self.assertEqual(mac_install_target(self.executable), self.target)
        self.assertIsNone(mac_install_target(other))
        self.assertIsNone(mac_install_target(translocated))
        self.assertIsNone(mac_install_target(self.root / "Snaploom"))
        with patch("snaploom.update_install_macos.os.access", return_value=False):
            self.assertIsNone(mac_install_target(self.executable))

    @contextmanager
    def mounted_image(self, package, env):
        self.assertEqual(package, self.dmg)
        self.assertEqual(env, {})
        yield self.mounted

    def test_checks_staged_signing_and_version_before_exchanging_bundles(self):
        make_bundle(self.replacement, "1.0.22")
        steps = []

        def system_command(argv, env, *, timeout):
            command = Path(argv[0]).name
            steps.append(command)
            self.assertEqual(env, {})
            if command == "ditto":
                self.assertEqual(Path(argv[1]), self.replacement)
                shutil.copytree(argv[1], argv[2], symlinks=True)
                return b""
            if command == "codesign":
                self.assertEqual(argv[1:4], ["--verify", "--deep", "--strict"])
                self.assertNotEqual(Path(argv[-1]), self.replacement)
                self.assertNotEqual(Path(argv[-1]), self.target)
                return b""
            self.assertEqual(argv[1], "--version")
            return b"1.0.22\n"

        def exchange(staged, target):
            self.assertEqual(target, self.target)
            self.assertTrue((staged / "Contents" / "Info.plist").is_file())
            self.assertTrue(self.executable.read_bytes().endswith(b"1.0.21"))
            self.assertEqual(staged.parent.parent, target.parent)
            steps.append("swap")

        with (
            patch("snaploom.update_install_macos._mount_disk_image", self.mounted_image),
            patch("snaploom.update_install_macos._native_machine", return_value="arm64"),
            patch("snaploom.update_install_macos._run", side_effect=system_command),
            patch("snaploom.update_install_macos._atomic_swap", side_effect=exchange),
        ):
            install_macos(self.dmg, self.executable, "1.0.22", {})
        self.assertEqual(steps, ["ditto", "codesign", "Snaploom", "swap"])
        self.assertEqual(set(self.root.glob(".snaploom-update-*")), set())

    def test_wrong_version_and_architecture_never_replace_the_old_application(self):
        make_bundle(self.replacement, "1.0.22")
        with (
            patch("snaploom.update_install_macos._mount_disk_image", self.mounted_image),
            patch("snaploom.update_install_macos._run") as run,
            patch("snaploom.update_install_macos._atomic_swap") as swap,
        ):
            with patch("snaploom.update_install_macos._native_machine", return_value="arm64"):
                with self.assertRaisesRegex(ValueError, "version"):
                    install_macos(self.dmg, self.executable, "1.0.23", {})
            with patch("snaploom.update_install_macos._native_machine", return_value="x86_64"):
                with self.assertRaisesRegex(ValueError, "processor"):
                    install_macos(self.dmg, self.executable, "1.0.22", {})
        run.assert_not_called()
        swap.assert_not_called()
        self.assertTrue(self.executable.read_bytes().endswith(b"1.0.21"))

    @unittest.skipUnless(sys.platform == "darwin", "Darwin directory exchange")
    def test_native_exchange_keeps_the_previous_bundle_at_the_staging_path(self):
        staged = self.root / "Staged.app"
        make_bundle(staged, "1.0.22")
        _atomic_swap(staged, self.target)
        self.assertTrue(self.executable.read_bytes().endswith(b"1.0.22"))
        self.assertTrue((staged / "Contents" / "MacOS" / "Snaploom").read_bytes().endswith(b"1.0.21"))
