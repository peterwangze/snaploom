"""Validate GitHub release metadata and select Snaploom update packages."""

from __future__ import annotations

import platform
import re
import sys
from dataclasses import dataclass


REPOSITORY = "peterwangze/snaploom"
PROJECT_URL = f"https://github.com/{REPOSITORY}"
RELEASES_URL = f"{PROJECT_URL}/releases"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECKSUM_NAME = "SHA256SUMS.txt"

_PACKAGE_SUFFIXES = {
    ("windows", "x64"): ("windows-x64-setup.exe", "windows-x64-portable.zip"),
    ("macos", "x64"): ("macos-x64.dmg",),
    ("macos", "arm64"): ("macos-arm64.dmg",),
    ("linux", "x64"): ("linux-x64-setup.run", "linux-x64.tar.gz"),
    ("linux", "arm64"): ("linux-arm64-setup.run", "linux-arm64.tar.gz"),
}
_SYSTEMS = {"windows": "windows", "darwin": "macos", "macos": "macos", "linux": "linux"}
_ARCHITECTURES = {
    "amd64": "x64", "x86_64": "x64", "x64": "x64",
    "aarch64": "arm64", "arm64": "arm64",
}
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
_API_DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})")
_CHECKSUM_LINE = re.compile(r"([0-9a-fA-F]{64})[ \t]+\*?([a-zA-Z0-9][a-zA-Z0-9._-]*)")


class UpdateMetadataError(ValueError):
    """A release, package or checksum could not be validated."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    assets: tuple[ReleaseAsset, ...]
    checksum: ReleaseAsset | None

    def newer_than(self, current: str) -> bool:
        return _version_parts(self.version) > _version_parts(current.removeprefix("v"))

    def packages(self, system: str | None = None, machine: str | None = None) -> tuple[ReleaseAsset, ...]:
        """Return compatible packages in install-then-portable order."""
        system = _SYSTEMS.get((system if system is not None else platform.system()).lower(), "")
        machine = machine if machine is not None else _native_machine(system)
        arch = _ARCHITECTURES.get(machine.lower(), "")
        names = _package_names(self.version, system, arch)
        assets = {asset.name: asset for asset in self.assets}
        return tuple(assets[name] for name in names if name in assets)


def _native_machine(system: str) -> str:
    machine = platform.machine()
    if system != "macos" or machine.lower() != "x86_64" or sys.platform != "darwin":
        return machine

    # A process launched through Rosetta still benefits from the native bundle.
    import ctypes

    translated = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(translated))
    try:
        result = ctypes.CDLL(None).sysctlbyname(
            b"sysctl.proc_translated", ctypes.byref(translated), ctypes.byref(size), None, 0,
        )
    except (AttributeError, OSError):
        return machine
    return "arm64" if result == 0 and translated.value == 1 else machine


def _version_parts(version: str) -> tuple[int, ...]:
    if _VERSION.fullmatch(version) is None:
        raise UpdateMetadataError("Expected a stable Snaploom version.")
    return tuple(int(part) for part in version.split("."))


def _package_names(version: str, system: str, arch: str) -> tuple[str, ...]:
    return tuple(f"Snaploom-{version}-{suffix}" for suffix in _PACKAGE_SUFFIXES.get((system, arch), ()))


def parse_release(payload: object) -> Release:
    """Decode a response from the repository's public latest-release endpoint."""
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateMetadataError("Expected a public stable release.")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateMetadataError("The release has no Snaploom version tag.")
    version = tag[1:]
    _version_parts(version)
    url = f"{RELEASES_URL}/tag/{tag}"
    if payload.get("html_url") != url:
        raise UpdateMetadataError("The release page is outside the Snaploom repository.")
    notes = payload.get("body")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise UpdateMetadataError("The release notes are invalid.")

    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise UpdateMetadataError("The release package list is invalid.")
    package_names = {
        name for system, arch in _PACKAGE_SUFFIXES for name in _package_names(version, system, arch)
    }
    packages = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or (name not in package_names and name != CHECKSUM_NAME):
            continue
        if item.get("state", "uploaded") != "uploaded":
            continue
        asset_url = f"{RELEASES_URL}/download/{tag}/{name}"
        size = item.get("size")
        if item.get("browser_download_url") != asset_url or type(size) is not int or size <= 0:
            raise UpdateMetadataError("A release package has invalid download information.")
        if name in packages:
            raise UpdateMetadataError("The release contains duplicate package names.")
        digest_value = item.get("digest")
        digest = None
        if digest_value is not None:
            match = _API_DIGEST.fullmatch(digest_value) if isinstance(digest_value, str) else None
            if match is None:
                raise UpdateMetadataError("A release package has an invalid SHA-256 digest.")
            digest = match[1].lower()
        packages[name] = ReleaseAsset(name, asset_url, size, digest)

    checksum = packages.pop(CHECKSUM_NAME, None)
    return Release(version, notes, url, tuple(packages.values()), checksum)


def expected_sha256(manifest: bytes, asset: ReleaseAsset) -> str:
    """Read the package digest and verify agreement with GitHub when present."""
    digests = []
    for line in manifest.decode("utf-8-sig", errors="replace").splitlines():
        match = _CHECKSUM_LINE.fullmatch(line.strip())
        if match is not None and match[2] == asset.name:
            digests.append(match[1].lower())
    if len(digests) != 1:
        raise UpdateMetadataError("The package must have one SHA-256 checksum in the release manifest.")
    if asset.digest is not None and asset.digest != digests[0]:
        raise UpdateMetadataError("The release package checksums disagree.")
    return digests[0]
