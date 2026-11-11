# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


root = Path(SPECPATH).parent
windows = sys.platform == "win32"
mac = sys.platform == "darwin"
backend = {"win32": "_win32", "darwin": "_darwin", "linux": "_xorg"}[sys.platform]
identity = os.environ.get("SNAPLOOM_MAC_IDENTITY") if mac else None
icon = os.environ["SNAPLOOM_BUILD_ICON"] if windows or mac else None
version = os.environ["SNAPLOOM_BUILD_VERSION"]

analysis = Analysis(
    [str(root / "run_snaploom.py")],
    pathex=[str(root)],
    datas=[(os.environ["SNAPLOOM_BUILD_ICON_PNG"], ".")]
    + collect_data_files("rapidocr_onnxruntime")
    + copy_metadata("rapidocr_onnxruntime"),
    hiddenimports=[f"pynput.keyboard.{backend}", f"pynput.mouse.{backend}"],
)
modules = PYZ(analysis.pure)

application = EXE(
    modules, analysis.scripts, exclude_binaries=True, name="Snaploom", console=False,
    upx=False, icon=icon, codesign_identity=identity,
)
executables = [application]
if windows:
    # Both launchers load the same runtime, models, and compiled Python modules.
    executables.append(EXE(
        modules, analysis.scripts, exclude_binaries=True, name="Snaploom-cli", console=True,
        upx=False, icon=icon,
    ))
collection = COLLECT(*executables, analysis.binaries, analysis.datas, upx=False, name="Snaploom")

if mac:
    bundle = BUNDLE(
        collection, name="Snaploom.app", icon=icon, bundle_identifier="io.github.peterwangze.snaploom",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
        },
    )
