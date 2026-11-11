"""Build and verify Snaploom offline packages on their target OS."""

import argparse
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from importlib import metadata
from pathlib import Path


ASSETS = Path(__file__).resolve().parent
ROOT = ASSETS.parent
RELEASE = ROOT / "release"
ARTIFACT_SUFFIXES = (".exe", ".zip", ".dmg", ".run", ".tar.gz")
VERSION_PATTERN = re.compile(r'^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', re.M)


def run(*args, **kwargs):
    subprocess.run([str(argument) for argument in args], check=True, cwd=ROOT, **kwargs)


def version():
    match = VERSION_PATTERN.search((ROOT / "snaploom" / "__init__.py").read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("No numeric Snaploom version was found")
    return match.group(1)


def architecture():
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported build architecture: {machine}")


def render_icon(destination, size):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer = QSvgRenderer(str(ASSETS / "snaploom.svg"))
    renderer.render(painter)
    painter.end()
    if not renderer.isValid() or not image.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to generate icon {destination}")


def make_icon(work):
    main_icon = work / "snaploom.png"
    render_icon(main_icon, 256)
    if sys.platform == "win32":
        images = []
        for size in (16, 24, 32, 48, 64, 128, 256):
            png = work / f"icon-{size}.png"
            render_icon(png, size)
            images.append((size % 256, png.read_bytes()))
        icon = work / "snaploom.ico"
        with icon.open("wb") as file:
            file.write(struct.pack("<HHH", 0, 1, len(images)))
            offset = 6 + 16 * len(images)
            for size, data in images:
                file.write(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset))
                offset += len(data)
            for _, data in images:
                file.write(data)
        return icon
    if sys.platform == "darwin":
        iconset = work / "snaploom.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            render_icon(iconset / f"icon_{size}x{size}.png", size)
            render_icon(iconset / f"icon_{size}x{size}@2x.png", size * 2)
        icon = work / "snaploom.icns"
        run("iconutil", "-c", "icns", "-o", icon, iconset)
        return icon
    return main_icon


def third_party_notices(destination):
    shutil.copytree(ASSETS / "licenses", destination)
    lines = ["# Bundled Python distributions", "", "See README.md in this directory for Qt sources.", ""]
    for package in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = package.metadata["Name"]
        package_name = name.lower().replace("_", "-")
        if package_name in {"pip", "setuptools", "pytest", "iniconfig", "pluggy", "pygments"}:
            continue
        if package_name.startswith("pyinstaller-hooks-"):
            continue
        lines.append(f"- {name} {package.version}")
        for relative in package.files or ():
            if ".." in relative.parts or relative.is_absolute():
                continue
            filename = relative.name.upper()
            if not filename.startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING", "THIRDPARTYNOTICES")):
                continue
            source = Path(package.locate_file(relative))
            if source.is_file():
                output = destination / name / Path(relative)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, output)
    (destination / "packages.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, destination / "Python-LICENSE.txt")


def freeze(work, icon):
    environment = os.environ.copy()
    environment.update(SNAPLOOM_BUILD_ICON=str(icon), SNAPLOOM_BUILD_ICON_PNG=str(work / "snaploom.png"),
                       SNAPLOOM_BUILD_VERSION=version())
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", work / "frozen",
        "--workpath", work / "work", ASSETS / "snaploom.spec", env=environment)
    return work / "frozen" / ("Snaploom.app" if sys.platform == "darwin" else "Snaploom")


def smoke(executable):
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    for option in ("--smoke-test", "--smoke-test-ocr"):
        run(executable, option, env=environment, timeout=90)


def sign_windows(executable):
    fingerprint = os.environ.get("SNAPLOOM_WINDOWS_CERT_SHA1")
    if not fingerprint:
        return
    signtool = os.environ.get("SNAPLOOM_SIGNTOOL") or shutil.which("signtool.exe")
    if not signtool:
        sdk = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits" / "10" / "bin"
        signtool = next(iter(sorted(sdk.glob("*/x64/signtool.exe"), reverse=True)), None)
    if not signtool:
        raise RuntimeError("Set SNAPLOOM_SIGNTOOL to the Windows SDK signtool.exe")
    timestamp_url = os.environ.get("SNAPLOOM_WINDOWS_TIMESTAMP_URL", "http://timestamp.digicert.com")
    run(signtool, "sign", "/sha1", fingerprint, "/fd", "SHA256", "/tr", timestamp_url, "/td", "SHA256", executable)


def package_windows(work, frozen, icon, notices, release, basename, verify):
    executable = frozen / "Snaploom.exe"
    sign_windows(executable)
    sign_windows(frozen / "Snaploom-cli.exe")
    shutil.copytree(notices, frozen / notices.name)
    shutil.make_archive(str(release / f"{basename}-portable"), "zip", frozen.parent, frozen.name)
    compiler = os.environ.get("SNAPLOOM_ISCC") or shutil.which("ISCC.exe")
    if not compiler:
        compiler = Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6" / "ISCC.exe"
    if not Path(compiler).is_file():
        raise RuntimeError("Install Inno Setup 6 or set SNAPLOOM_ISCC to ISCC.exe")
    output_name = f"{basename}-setup"
    run(compiler, f"/DAppVersion={version()}", f"/DPayloadDir={frozen}", f"/DReleaseDir={release}",
        f"/DOutputName={output_name}", f"/DIconPath={icon}", ASSETS / "windows.iss")
    setup = release / f"{output_name}.exe"
    sign_windows(setup)
    if verify:
        installed = work / "offline-install"
        run(setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={installed}", timeout=150)
        try:
            smoke(installed / executable.name)
            run(installed / "Snaploom-cli.exe", "--version", timeout=30)
        finally:
            uninstaller = installed / "unins000.exe"
            if uninstaller.is_file():
                run(uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", timeout=90)


def package_mac(work, frozen, notices, release, basename, verify):
    identity = os.environ.get("SNAPLOOM_MAC_IDENTITY")
    signature = ["--options", "runtime", "--timestamp"] if identity else []
    run("codesign", "--force", "--sign", identity or "-", *signature, frozen)
    run("codesign", "--verify", "--deep", "--strict", frozen)
    image_contents = work / "disk-image"
    image_contents.mkdir()
    shutil.move(frozen, image_contents / frozen.name)
    shutil.copytree(notices, image_contents / notices.name)
    (image_contents / "Applications").symlink_to("/Applications")
    disk_image = release / f"{basename}.dmg"
    run("hdiutil", "create", "-quiet", "-volname", "Snaploom", "-srcfolder", image_contents, "-format", "UDZO", disk_image)
    if identity:
        run("codesign", "--sign", identity, "--timestamp", disk_image)
    notary = os.environ.get("SNAPLOOM_NOTARY_PROFILE")
    if notary:
        if not identity:
            raise RuntimeError("Notarization requires SNAPLOOM_MAC_IDENTITY")
        run("xcrun", "notarytool", "submit", disk_image, "--keychain-profile", notary, "--wait")
        run("xcrun", "stapler", "staple", disk_image)
    if verify:
        mount = work / "dmg-mount"
        mount.mkdir()
        run("hdiutil", "attach", "-quiet", "-readonly", "-nobrowse", "-mountpoint", mount, disk_image)
        try:
            smoke(mount / "Snaploom.app" / "Contents" / "MacOS" / "Snaploom")
        finally:
            run("hdiutil", "detach", "-quiet", mount)


def package_linux(work, frozen, icon, notices, release, basename, verify):
    install_root = work / "linux-install"
    install_root.mkdir()
    shutil.move(frozen, install_root / frozen.name)
    shutil.copyfile(icon, install_root / "snaploom.png")
    install = install_root / "install.sh"
    install.write_bytes((ASSETS / "linux-install.sh").read_bytes().replace(b"\r\n", b"\n"))
    install.chmod(0o755)
    shutil.move(notices, install_root / frozen.name / notices.name)
    archive = release / f"{basename}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=6) as file:
        for path in sorted(install_root.iterdir()):
            file.add(path, arcname=path.name)

    installer = release / f"{basename}-setup.run"
    with installer.open("wb") as output, archive.open("rb") as payload:
        output.write((ASSETS / "linux-run.sh").read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n")
        shutil.copyfileobj(payload, output)
    installer.chmod(0o755)

    if verify:
        environment = os.environ.copy()
        home = work / "test home"
        environment.update(HOME=str(home), XDG_DATA_HOME=str(home / "data"))
        installed = home / "data" / "snaploom" / "app"
        run("sh", installer, env=environment, timeout=150)
        smoke(installed / "Snaploom")
        run("sh", installed / "uninstall.sh", "--uninstall", env=environment, timeout=90)
        if installed.exists():
            raise RuntimeError("The Linux uninstaller left the app in place")


def checksums(release):
    artifacts = sorted(path for path in release.glob("Snaploom-*") if path.name.endswith(ARTIFACT_SUFFIXES))
    if not artifacts:
        raise RuntimeError(f"No release artifacts found in {release}")
    with (release / "SHA256SUMS.txt").open("w", encoding="utf-8", newline="\n") as checksums_file:
        for artifact in artifacts:
            with artifact.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            checksums_file.write(f"{digest}  {artifact.name}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Install the package and verify GUI and offline OCR")
    parser.add_argument("--checksums-only", action="store_true")
    args = parser.parse_args()
    RELEASE.mkdir(exist_ok=True)
    if args.checksums_only:
        checksums(RELEASE)
        return
    work_root = ROOT / "build" / "packaging"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root) as temporary:
        work = Path(temporary)
        target = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
        basename = f"Snaploom-{version()}-{target}-{architecture()}"
        staging = work / "release"
        staging.mkdir()
        icon = make_icon(work)
        notices = work / "THIRD_PARTY_NOTICES"
        third_party_notices(notices)
        frozen = freeze(work, icon)
        if sys.platform == "win32":
            package_windows(work, frozen, icon, notices, staging, basename, args.smoke)
        elif sys.platform == "darwin":
            package_mac(work, frozen, notices, staging, basename, args.smoke)
        else:
            package_linux(work, frozen, icon, notices, staging, basename, args.smoke)
        for path in staging.iterdir():
            path.replace(RELEASE / path.name)
    checksums(RELEASE)


if __name__ == "__main__":
    main()
