import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from snaploom import update_install_winlinux as installer


def _runtime(root: Path, windows: bool) -> Path:
    root.mkdir()
    for name in ("_internal", "THIRD_PARTY_NOTICES"):
        (root / name).mkdir()
        (root / name / "version.txt").write_text("old")
    executable = root / ("Snaploom.exe" if windows else "Snaploom")
    executable.write_text("old")
    if windows:
        (root / "Snaploom-cli.exe").write_text("old")
    return executable


def _write_zip(path: Path, *, unsafe=False) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("_internal/version.txt", "THIRD_PARTY_NOTICES/version.txt",
                     "Snaploom.exe", "Snaploom-cli.exe"):
            archive.writestr(f"Snaploom/{name}", "new")
        if unsafe:
            archive.writestr("Snaploom/../../unexpected.txt", "outside")


def _write_run(path: Path) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name in ("_internal/version.txt", "THIRD_PARTY_NOTICES/version.txt", "Snaploom"):
            member = tarfile.TarInfo(f"Snaploom/{name}")
            member.size = 3
            member.mode = 0o755 if name == "Snaploom" else 0o644
            archive.addfile(member, io.BytesIO(b"new"))
    path.write_bytes(b"#!/bin/sh\nexit 1\n__SNAPLOOM_PAYLOAD_BELOW__\n" + stream.getvalue())


def _assert_new(executable, version, env):
    assert executable.read_text() == "new"


def test_portable_installer_changes_runtime_and_keeps_unknown_files(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer, "_cli_version", _assert_new)
    executable = _runtime(tmp_path / "App with spaces", windows=True)
    package = tmp_path / "Snaploom-9.9.9-windows-x64-portable.zip"
    _write_zip(package)
    workspace = executable.parent / "private folder"
    workspace.mkdir()
    own_file = workspace / "draft.txt"
    own_file.write_text("Keep this")

    assert installer.installation_kind(executable) == "portable"
    installer.install_windows_or_linux(package, executable, "9.9.9", os.environ.copy())
    assert executable.read_text() == "new"
    assert (executable.parent / "_internal/version.txt").read_text() == "new"
    assert own_file.read_text() == "Keep this"


def test_mismatched_or_unsafe_update_keeps_current_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    executable = _runtime(tmp_path / "Snaploom", windows=True)
    package = tmp_path / "Snaploom-9.9.9-windows-x64-portable.zip"
    _write_zip(package, unsafe=True)
    with pytest.raises(installer.InstallationError, match="file name"):
        installer.install_windows_or_linux(package, executable, "9.9.9", os.environ.copy())
    with pytest.raises(installer.InstallationError, match="selected package"):
        installer.install_windows_or_linux(package.with_suffix(".exe"), executable, "9.9.9", os.environ.copy())
    assert executable.read_text() == "old"
    assert not (tmp_path / "unexpected.txt").exists()


def test_custom_profile_in_runtime_blocks_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    executable = _runtime(tmp_path / "Snaploom", windows=True)
    profile = executable.parent / "_internal" / "my data"
    profile.mkdir()
    (profile / "draft.txt").write_text("keep")
    monkeypatch.setenv("SNAPLOOM_DATA_DIR", str(profile))
    assert installer.installation_kind(executable) is None
    with pytest.raises(installer.InstallationError, match="profile is inside"):
        installer.install_windows_or_linux(tmp_path / "update.zip", executable, "9.9.9", os.environ.copy())
    assert (profile / "draft.txt").read_text() == "keep"


def test_final_version_verification_failure_rolls_back_portable_install(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    executable = _runtime(tmp_path / "Snaploom", windows=True)
    package = tmp_path / "Snaploom-9.9.9-windows-x64-portable.zip"
    _write_zip(package)

    def verify(candidate, version, env):
        if candidate == executable:
            raise installer.InstallationError("Installed version could not be verified")
        _assert_new(candidate, version, env)

    monkeypatch.setattr(installer, "_cli_version", verify)
    with pytest.raises(installer.InstallationError, match="could not be verified"):
        installer.install_windows_or_linux(package, executable, "9.9.9", os.environ.copy())
    assert executable.read_text() == "old"
    assert (executable.parent / "_internal/version.txt").read_text() == "old"


def test_inno_uses_current_path_and_restores_runtime_after_setup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    executable = _runtime(tmp_path / "Custom App Folder", windows=True)
    uninstaller = executable.parent / "unins000.exe"
    uninstaller.write_text("old")
    uninstaller.with_suffix(".dat").write_text("old")
    package = tmp_path / "Snaploom-9.9.9-windows-x64-setup.exe"
    package.touch()

    def failed_setup(command, **kwargs):
        assert f"/DIR={executable.parent}" in command
        assert "/NOCLOSEAPPLICATIONS" in command and "/NORESTART" in command
        executable.write_text("partial")
        uninstaller.write_text("partial")
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(installer.subprocess, "run", failed_setup)
    assert installer.installation_kind(executable) == "installer"
    with pytest.raises(installer.InstallationError, match="code 5"):
        installer.install_windows_or_linux(package, executable, "9.9.9", os.environ.copy())
    assert executable.read_text() == "old"
    assert uninstaller.read_text() == "old"


def test_linux_installer_upgrades_runtime_and_preserves_installation_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer, "_cli_version", _assert_new)
    executable = _runtime(tmp_path / "app", windows=False)
    (executable.parent / ".snaploom-installed").touch()
    uninstaller = executable.parent / "uninstall.sh"
    uninstaller.write_text("current launcher")
    package = tmp_path / "Snaploom-9.9.9-linux-x64-setup.run"
    _write_run(package)
    installer.install_windows_or_linux(package, executable, "9.9.9", os.environ.copy())
    assert installer.installation_kind(executable) == "installer"
    assert executable.read_text() == "new"
    assert uninstaller.read_text() == "current launcher"
