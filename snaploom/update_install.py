"""Hand off an update to a helper outside the running application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .updates import Release, ReleaseAsset


HANDOFF = "update-install.json"
WORK_DIR = ".update-work"
HELPER_NAME = "Snaploom-update.exe" if sys.platform == "win32" else "Snaploom-update"
WORKER_NAME = re.compile(r"worker-[a-z0-9_]{8}")


@dataclass(frozen=True)
class InstallPlan:
    parent_pid: int
    profile: str
    executable: str
    package: str
    asset_name: str
    size: int
    sha256: str
    version: str
    kind: str


def installation_kind(executable: Path) -> str | None:
    if sys.platform == "darwin":
        from .update_install_macos import mac_install_target

        return "installer" if mac_install_target(executable) is not None else None
    if sys.platform in {"win32", "linux"}:
        from .update_install_winlinux import installation_kind as platform_kind

        return platform_kind(executable)
    return None


def package_kind(name: str) -> str:
    return "portable" if name.endswith((".zip", ".tar.gz")) else "installer"


def current_installation_kind() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    return installation_kind(Path(sys.executable))


def _atomic_json(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_handoff(profile: Path) -> dict:
    try:
        data = json.loads((profile / HANDOFF).read_text(encoding="utf-8"))
        if (
            isinstance(data, dict) and data.get("status") in {"installing", "installed", "failed"}
            and isinstance(data.get("worker"), str) and WORKER_NAME.fullmatch(data["worker"])
            and type(data.get("pid")) is int and 0 < data["pid"] <= 0xffffffff
        ):
            return data
    except (OSError, ValueError):
        pass
    return {}


def process_running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = wintypes.HANDLE, wintypes.DWORD
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied can mean the process is running.
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clean_environment(profile: Path | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if profile is not None:
        env["SNAPLOOM_DATA_DIR"] = str(profile)
    return env


def finish_handoff(profile: Path, handoff: dict) -> bool:
    """Clean up the copied helper after it has released its executable."""
    if read_handoff(profile) != handoff:
        return True
    if process_running(handoff["pid"]):
        return False
    stage = profile / WORK_DIR / handoff["worker"]
    log = stage / "log.txt"
    if handoff["status"] != "installed" and log.is_file():
        shutil.copyfile(log, profile / "update-install.log")
    if stage.is_dir():
        shutil.rmtree(stage)
    (profile / HANDOFF).unlink(missing_ok=True)
    return True


def start_update(profile: Path, release: Release, asset: ReleaseAsset, package: Path, sha256: str) -> None:
    """Launch a copied updater and register the handoff before the caller quits."""
    from . import VERSION

    exe = Path(sys.executable).resolve(strict=True)
    kind = current_installation_kind()
    if kind is None or asset not in release.packages() or package_kind(asset.name) != kind or not release.newer_than(VERSION):
        raise ValueError("The downloaded package does not match this Snaploom installation.")
    if not re.fullmatch(r"[a-f0-9]{64}", sha256) or (asset.digest and asset.digest != sha256):
        raise ValueError("The downloaded package could not be verified.")
    package = package.absolute()
    if not package.is_file() or package.stat().st_size != asset.size:
        raise ValueError("The downloaded package has changed or is missing.")
    helper = Path(sys._MEIPASS) / HELPER_NAME
    if not helper.is_file():
        raise OSError("The installer helper is missing.")
    profile = profile.resolve(strict=True)
    if sys.platform == "darwin" and profile.is_relative_to(exe.parents[2]):
        raise ValueError("The workspace must be outside the application directory to install an update.")
    previous = read_handoff(profile)
    if previous and process_running(previous["pid"]):
        raise OSError("An update is already running.")
    root = profile / WORK_DIR
    root.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="worker-", dir=root))
    plan = InstallPlan(os.getpid(), str(profile), str(exe), str(package), asset.name, asset.size, sha256, release.version, kind)
    try:
        worker = stage / HELPER_NAME
        shutil.copy2(helper, worker)
        _atomic_json(stage / "plan.json", asdict(plan))
        with (stage / "log.txt").open("ab") as log:
            proc = subprocess.Popen(
                [str(worker), str(stage / "plan.json")], cwd=stage, env=clean_environment(profile),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if sys.platform == "win32" else {"start_new_session": True}),
            )
        _atomic_json(profile / HANDOFF, {"status": "installing", "worker": stage.name, "pid": proc.pid})
    except (OSError, ValueError):
        # Without a matching handoff record the worker never applies the package.
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _copy_verified(plan: InstallPlan, stage: Path) -> Path:
    if (
        not re.fullmatch(r"[a-f0-9]{64}", plan.sha256) or type(plan.size) is not int or plan.size <= 0
        or not re.fullmatch(r"Snaploom-[a-zA-Z0-9.-]+", plan.asset_name)
    ):
        raise ValueError("Invalid update package.")
    path = stage / plan.asset_name
    digest = hashlib.sha256()
    size = 0
    with Path(plan.package).open("rb") as source, path.open("xb") as target:
        while block := source.read(1024 * 1024):
            size += len(block)
            if size > plan.size:
                raise ValueError("The package size changed after download.")
            target.write(block)
            digest.update(block)
    if size != plan.size or digest.hexdigest() != plan.sha256:
        raise ValueError("The package no longer matches the verified download.")
    return path


def _restart(executable: Path, profile: Path, env: dict[str, str]) -> None:
    arguments = ["--resume-update", str(profile)]
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", "-n", str(executable.parents[2]), "--args", *arguments],
                       env=env, check=True, timeout=30)
    else:
        subprocess.Popen([str(executable), *arguments], cwd=executable.parent, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def worker_main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    request = Path(args[0]).resolve()
    stage = request.parent
    plan = InstallPlan(**json.loads(request.read_text(encoding="utf-8")))
    profile = Path(plan.profile).resolve(strict=True)
    if request.name != "plan.json" or stage.parent != profile / WORK_DIR or not WORKER_NAME.fullmatch(stage.name):
        return 2
    executable = Path(plan.executable)
    env = clean_environment(profile)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
    deadline = time.monotonic() + 45
    while process_running(plan.parent_pid) and time.monotonic() < deadline:
        time.sleep(0.15)
    handoff = read_handoff(profile)
    if handoff.get("status") != "installing" or handoff.get("worker") != stage.name:
        return 2
    if process_running(plan.parent_pid):
        _atomic_json(profile / HANDOFF, handoff | {"status": "failed"})
        return 1
    status = "failed"
    try:
        if installation_kind(executable) != plan.kind or package_kind(plan.asset_name) != plan.kind:
            raise ValueError("The application installation changed.")
        package = _copy_verified(plan, stage)
        if sys.platform == "darwin":
            from .update_install_macos import install_macos

            install_macos(package, executable, plan.version, env)
        else:
            from .update_install_winlinux import install_windows_or_linux

            install_windows_or_linux(package, executable, plan.version, env)
        status = "installed"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Snaploom update failed: {error}", file=sys.stderr, flush=True)
    finally:
        # Keep the user's download, but remove the verified working copy.
        if re.fullmatch(r"Snaploom-[a-zA-Z0-9.-]+", plan.asset_name):
            (stage / plan.asset_name).unlink(missing_ok=True)
        _atomic_json(profile / HANDOFF, handoff | {"status": status})
    _restart(executable, profile, env)
    return int(status != "installed")
