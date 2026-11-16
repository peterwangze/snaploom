import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from snaploom import update_install as install


@pytest.mark.parametrize("asset_change", [False, True])
def test_handoff_waits_and_restarts_only_after_verified_package_is_applied(tmp_path, asset_change):
    stage = tmp_path / install.WORK_DIR / "worker-12345678"
    stage.mkdir(parents=True)
    content = b"Verified Snaploom installer"
    package = tmp_path / "download"
    package.write_bytes(b"x" * len(content) if asset_change else content)
    extension = {"darwin": "dmg", "win32": "exe", "linux": "run"}[sys.platform]
    plan = install.InstallPlan(
        124, str(tmp_path), str(tmp_path / "Snaploom"), str(package),
        f"Snaploom-99.0.0-test.{extension}", len(content), hashlib.sha256(content).hexdigest(), "99.0.0", "installer",
    )
    request = stage / "plan.json"
    request.write_text(json.dumps(asdict(plan)), encoding="utf-8")
    (tmp_path / install.HANDOFF).write_text(
        json.dumps({"status": "installing", "worker": stage.name, "pid": 125}), encoding="utf-8",
    )
    applied = []

    def apply(staged_package, executable, version, env):
        assert staged_package.parent == stage
        assert staged_package.read_bytes() == content
        assert executable == Path(plan.executable) and version == "99.0.0"
        assert env["SNAPLOOM_DATA_DIR"] == str(tmp_path)
        applied.append(True)

    module = ("snaploom.update_install_macos.install_macos" if sys.platform == "darwin"
              else "snaploom.update_install_winlinux.install_windows_or_linux")
    with (
        patch.object(install, "installation_kind", return_value="installer"),
        patch.object(install, "process_running", side_effect=[True, False, False]) as running,
        patch.object(install.time, "sleep") as waiting,
        patch(module, side_effect=apply),
        patch.object(install, "_restart") as restart,
    ):
        assert install.worker_main([str(request)]) == int(asset_change)
    assert len(applied) == int(not asset_change)
    assert all(call.args == (124,) for call in running.call_args_list)
    waiting.assert_called_once()
    assert install.read_handoff(tmp_path)["status"] == ("failed" if asset_change else "installed")
    assert not (stage / plan.asset_name).exists()
    assert package.exists()
    restart.assert_called_once()
    assert restart.call_args.args[:2] == (Path(plan.executable), tmp_path)
