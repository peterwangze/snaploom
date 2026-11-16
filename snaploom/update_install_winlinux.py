"""Apply a verified official Windows or Linux update after Snaploom exits."""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


_COMMON_FILES = ("_internal", "THIRD_PARTY_NOTICES")
_WINDOWS_FILES = (*_COMMON_FILES, "Snaploom.exe", "Snaploom-cli.exe")
_LINUX_FILES = (*_COMMON_FILES, "Snaploom")
_INNO_UNINSTALLER = re.compile(r"unins[0-9]{3}\.(?:exe|dat|msg)", re.IGNORECASE)
_RUN_PAYLOAD_MARKER = b"__SNAPLOOM_PAYLOAD_BELOW__\n"
_RUN_HEADER_LIMIT = 64 * 1024


class InstallationError(RuntimeError):
    """An update could not be safely installed."""


class _RecoveryError(InstallationError):
    pass


def _own_runtime(executable: Path, names: tuple[str, ...]) -> bool:
    library = executable.parent / "_internal"
    return (
        executable.name in names
        and executable.is_file()
        and library.is_dir()
        and not library.is_symlink()
    )


def installation_kind(executable: Path) -> str | None:
    """Identify recognizable official layouts on the running platform."""
    root = executable.parent
    if _profile_collides(root, os.environ):
        return None
    if sys.platform == "win32" and _own_runtime(executable, ("Snaploom.exe", "Snaploom-cli.exe")):
        if not all((root / name).is_file() for name in _WINDOWS_FILES[-2:]):
            return None
        for candidate in root.glob("unins*.exe"):
            if _INNO_UNINSTALLER.fullmatch(candidate.name) and candidate.with_suffix(".dat").is_file():
                return "installer"
        return "portable"
    if sys.platform.startswith("linux") and _own_runtime(executable, ("Snaploom",)):
        marker = root / ".snaploom-installed"
        return "installer" if marker.is_file() and not marker.is_symlink() else "portable"
    return None


def _profile_collides(root: Path, env) -> bool:
    configured = env.get("SNAPLOOM_DATA_DIR")
    if not configured:
        return False
    profile = Path(configured).expanduser().resolve()
    return any(profile.is_relative_to((root / name).resolve()) for name in _COMMON_FILES)


def _cli_version(executable: Path, target_version: str, env: dict[str, str]) -> None:
    cli = executable.with_name("Snaploom-cli.exe") if sys.platform == "win32" else executable
    process = subprocess.run(
        [str(cli), "--version"],
        cwd=cli.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if process.returncode or process.stdout.strip() != target_version:
        raise InstallationError(f"The installed program did not report version {target_version}.")


def _replacement_names(root: Path, names: tuple[str, ...]) -> tuple[str, ...]:
    if sys.platform != "win32":
        return names
    return names + tuple(path.name for path in root.glob("unins*") if _INNO_UNINSTALLER.fullmatch(path.name))


def _restore_files(root: Path, backup: Path, work: Path, names: list[str]) -> None:
    """Move failed replacements aside before restoring the original entries."""
    failed = work / "failed"
    failed.mkdir()
    root.mkdir(exist_ok=True)
    for name in names:
        destination = root / name
        if os.path.lexists(destination):
            os.replace(destination, failed / name)
        original = backup / name
        if os.path.lexists(original):
            os.replace(original, destination)


def _recover(root: Path, backup: Path, work: Path, names: list[str]) -> None:
    try:
        _restore_files(root, backup, work, names)
    except OSError as error:
        # The caller keeps the backup available if restoration itself fails.
        raise _RecoveryError(f"Could not restore the previous application files. Backup: {backup}") from error


def _run_inno(package: Path, executable: Path, target_version: str, env: dict[str, str], work: Path) -> None:
    root = executable.parent
    names_before = _replacement_names(root, _WINDOWS_FILES)
    backup = work / "previous"
    backup.mkdir()
    for name in names_before:
        original = root / name
        if original.is_dir():
            shutil.copytree(original, backup / name, symlinks=True)
        elif os.path.lexists(original):
            shutil.copy2(original, backup / name, follow_symlinks=False)
    try:
        process = subprocess.run(
            [
                str(package), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-",
                "/NORESTART", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/RESTARTEXITCODE=16384",
                f"/DIR={root}",
            ],
            cwd=work,
            env=env,
            timeout=600,
        )
        if process.returncode:
            raise InstallationError(f"Snaploom Setup exited with code {process.returncode}.")
        _cli_version(executable, target_version, env)
    except BaseException:
        names = list(dict.fromkeys((*names_before, *_replacement_names(root, _WINDOWS_FILES))))
        _recover(root, backup, work, names)
        raise


def _extract_zip(package: Path, stage: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        member_names = set()
        for member in archive.infolist():
            name = member.filename.rstrip("/")
            parts = PurePosixPath(name).parts
            if (
                not parts or parts[0] != "Snaploom"
                or name != "/".join(parts) or ".." in parts or "\\" in name or ":" in name
                or name.lower() in member_names
            ):
                raise InstallationError("The Windows update archive has an unexpected file name.")
            if len(parts) > 1 and parts[1] not in _WINDOWS_FILES:
                raise InstallationError("The Windows update archive contains an unknown application entry.")
            member_names.add(name.lower())
        archive.extractall(stage)


def _tar_runtime_members(archive: tarfile.TarFile):
    for member in archive:
        parts = PurePosixPath(member.name).parts
        if parts and parts[0] == "Snaploom":
            if (
                member.name != "/".join(parts) or ".." in parts
                or (len(parts) > 1 and parts[1] not in _LINUX_FILES)
                or not (member.isfile() or member.isdir() or member.issym())
            ):
                raise InstallationError("The Linux update archive has an unexpected application entry.")
            if member.issym():
                link = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                allowed = "Snaploom/" + parts[1] + "/" if len(parts) > 1 else ""
                if not allowed or not link.startswith(allowed):
                    raise InstallationError("The Linux update archive contains a link outside its own entry.")
            yield member
        elif member.name not in {"install.sh", "snaploom.png"} or not member.isfile():
            raise InstallationError("The Linux update archive contains an unexpected file.")


def _extract_linux(package: Path, stage: Path) -> None:
    with package.open("rb") as stream:
        if package.name.endswith(".run"):
            for _ in range(256):
                line = stream.readline(4096)
                if line == _RUN_PAYLOAD_MARKER:
                    break
                if not line or not line.endswith(b"\n") or stream.tell() >= _RUN_HEADER_LIMIT:
                    raise InstallationError("The Linux installer has no recognized payload.")
            else:
                raise InstallationError("The Linux installer has no recognized payload.")
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            archive.extractall(stage, members=_tar_runtime_members(archive), filter="data")


def _replace_payload(
    executable: Path, candidate: Path, target_version: str, env: dict[str, str], work: Path,
) -> None:
    root = executable.parent
    names = _WINDOWS_FILES if sys.platform == "win32" else _LINUX_FILES
    if not all((candidate / name).exists() and not (candidate / name).is_symlink() for name in names):
        raise InstallationError("The update package has an incomplete Snaploom runtime.")
    _cli_version(candidate / executable.name, target_version, env)

    backup = work / "previous"
    backup.mkdir()
    changed = []
    try:
        for name in names:
            old = root / name
            if os.path.lexists(old):
                os.replace(old, backup / name)
            changed.append(name)
            os.replace(candidate / name, old)
        _cli_version(executable, target_version, env)
    except BaseException:
        _recover(root, backup, work, changed)
        raise


def install_windows_or_linux(
    package: Path, executable: Path, target_version: str, env: dict[str, str],
) -> None:
    """Install a matching verified package, rolling back failures where possible."""
    if _profile_collides(executable.parent, env):
        raise InstallationError("The Snaploom profile is inside the runtime directory that needs to be replaced.")
    kind = installation_kind(executable)
    if sys.platform == "win32":
        suffix = {"installer": ".exe", "portable": ".zip"}.get(kind)
    elif sys.platform.startswith("linux"):
        suffix = {"installer": ".run", "portable": ".tar.gz"}.get(kind)
    else:
        suffix = None
    if suffix is None or not package.name.lower().endswith(suffix):
        raise InstallationError("The selected package does not match this Snaploom installation.")

    # Stage on the same volume as the target so replacement consists of renames.
    work = Path(tempfile.mkdtemp(prefix=".snaploom-update-", dir=executable.parent.parent))
    keep_recovery = False
    try:
        if sys.platform == "win32" and kind == "installer":
            _run_inno(package, executable, target_version, env, work)
        else:
            stage = work / "download"
            stage.mkdir()
            if sys.platform == "win32":
                _extract_zip(package, stage)
            else:
                _extract_linux(package, stage)
            _replace_payload(executable, stage / "Snaploom", target_version, env, work)
    except _RecoveryError:
        keep_recovery = True
        raise
    finally:
        if not keep_recovery:
            shutil.rmtree(work, ignore_errors=True)
