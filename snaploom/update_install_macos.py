"""Prepare and replace a signed Snaploom app bundle on macOS."""

from __future__ import annotations

import ctypes
import os
import plistlib
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from xml.parsers.expat import ExpatError

from .updates import _native_machine


_BUNDLE_ID = "io.github.peterwangze.snaploom"
_BINARY = Path("Contents/MacOS/Snaploom")
_INFO = Path("Contents/Info.plist")
_CPU_TYPES = {"arm64": 0x0100000C, "x86_64": 0x01000007}
_RENAME_SWAP = 0x00000002


def _bundle_info(bundle: Path) -> dict:
    info = bundle / _INFO
    executable = bundle / _BINARY
    if (bundle.is_symlink() or info.is_symlink() or not info.is_file()
            or executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise ValueError("Snaploom.app is not a complete application bundle.")
    if not info.resolve().is_relative_to(bundle.resolve()) or not executable.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("Snaploom.app contains an invalid executable or Info.plist location.")
    try:
        with info.open("rb") as source:
            values = plistlib.load(source)
    except ExpatError as error:
        raise ValueError("The Snaploom bundle's Info.plist is malformed.") from error
    if (not isinstance(values, dict)
            or values.get("CFBundlePackageType") != "APPL"
            or values.get("CFBundleIdentifier") != _BUNDLE_ID
            or values.get("CFBundleExecutable") != _BINARY.name):
        raise ValueError("The bundle identity does not match Snaploom.")
    return values


def mac_install_target(executable: Path) -> Path | None:
    """Return the writable installed bundle represented by the running executable."""
    executable = Path(executable)
    if len(executable.parents) < 3 or tuple(executable.parts[-3:]) != _BINARY.parts:
        return None
    target = executable.parents[2]
    if target.suffix.lower() != ".app" or target.is_symlink():
        return None
    try:
        target = target.resolve(strict=True)
        if ("AppTranslocation" in target.parts
                or not os.access(target.parent, os.W_OK | os.X_OK)
                or not os.access(target, os.W_OK)):
            return None
        _bundle_info(target)
    except (OSError, ValueError):
        return None
    return target


def _mach_cpus(executable: Path) -> set[int]:
    """Read the architecture from a Mach-O executable or its universal header."""
    with executable.open("rb") as binary:
        magic = binary.read(4)
        if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            order = "little" if magic == b"\xcf\xfa\xed\xfe" else "big"
            return {int.from_bytes(binary.read(4), order)}
        universal = {
            b"\xca\xfe\xba\xbe": ("big", 20), b"\xbe\xba\xfe\xca": ("little", 20),
            b"\xca\xfe\xba\xbf": ("big", 32), b"\xbf\xba\xfe\xca": ("little", 32),
        }
        if magic not in universal:
            return set()
        order, stride = universal[magic]
        count = int.from_bytes(binary.read(4), order)
        if not 1 <= count <= 32:
            return set()
        entries = binary.read(count * stride)
        if len(entries) != count * stride:
            return set()
        return {int.from_bytes(entries[index:index + 4], order) for index in range(0, len(entries), stride)}


def _run(arguments, env, *, timeout):
    try:
        return subprocess.run(
            arguments, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=timeout,
        ).stdout
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"{Path(arguments[0]).name}: {message[-1500:] or 'operation failed'}") from error


@contextmanager
def _mount_disk_image(package: Path, env):
    # Keep the mount out of the staging tree so a failed detach never causes
    # recursive cleanup of a mounted filesystem.
    mount = Path(tempfile.mkdtemp(prefix="snaploom-update-mount-")).resolve()
    try:
        _run(
            ["/usr/bin/hdiutil", "attach", "-quiet", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint",
             str(mount), str(package)],
            env, timeout=120,
        )
        yield mount
    finally:
        try:
            if os.path.ismount(mount):
                try:
                    _run(["/usr/bin/hdiutil", "detach", "-quiet", str(mount)], env, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    _run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", str(mount)], env, timeout=30)
        finally:
            if not os.path.ismount(mount):
                try:
                    mount.rmdir()
                except FileNotFoundError:
                    pass


def _atomic_swap(staged: Path, installed: Path) -> None:
    # Darwin can exchange two directory entries in one filesystem operation.
    # The installed path remains usable if the update fails or is interrupted.
    rename = ctypes.CDLL(None, use_errno=True).renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(staged), os.fsencode(installed), _RENAME_SWAP) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(installed))


def _validate_update_bundle(bundle: Path, version: str) -> None:
    values = _bundle_info(bundle)
    if values.get("CFBundleVersion") != version or values.get("CFBundleShortVersionString") != version:
        raise ValueError("The macOS bundle does not match the downloaded release version.")
    cpu = _CPU_TYPES.get(_native_machine("macos").lower())
    if cpu is None or cpu not in _mach_cpus(bundle / _BINARY):
        raise ValueError("The downloaded app does not support this Mac's processor.")


def install_macos(package: Path, executable: Path, target_version: str, env: dict[str, str]) -> None:
    """Atomically replace the existing app with the validated bundle in a verified DMG."""
    target = mac_install_target(executable)
    if target is None or package.suffix.lower() != ".dmg" or not package.is_file():
        raise ValueError("The update requires a writable Snaploom installation and a verified DMG.")
    original = target.stat()

    with tempfile.TemporaryDirectory(prefix=".snaploom-update-", dir=target.parent, ignore_cleanup_errors=True) as work:
        staged = Path(work) / "Snaploom.app"
        with _mount_disk_image(package, env) as mount:
            source = mount / "Snaploom.app"
            _validate_update_bundle(source, target_version)
            _run(["/usr/bin/ditto", str(source), str(staged)], env, timeout=180)
        _validate_update_bundle(staged, target_version)
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)], env, timeout=120)
        reported = _run([str(staged / _BINARY), "--version"], env, timeout=35)
        if reported.strip() != target_version.encode("ascii"):
            raise ValueError("The replacement executable could not report the expected release version.")
        if mac_install_target(executable) != target or not os.path.samestat(original, target.stat()):
            raise OSError("The installed bundle changed while Snaploom was preparing the update.")
        _atomic_swap(staged, target)
