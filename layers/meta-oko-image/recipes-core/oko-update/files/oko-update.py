#!/usr/bin/env python3
# Copyright 2026 Satisfanly Ltd
#
# OKO OS is a product of Satisfanly Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Install an OKO release into the inactive A/B root slot.

The shared OKO_BOOT filesystem keeps one kernel per root slot.
The active root and active boot kernel are never modified during installation.
Bootloader configuration is switched only after the inactive root and boot
payload have been fully written and verified.
"""

from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import json
import os
import posixpath
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, NoReturn


CONFIG_PATH = Path("/etc/oko-update.conf")
DEFAULT_ENDPOINT = "https://cdn-oko.satisfanly.com/oko-update/latest"
DEFAULT_KERNEL_ARGS = (
    "rw rootwait roottimeout=30 rootfstype=ext4 "
    "console=tty0 console=ttyS0,115200n8"
)

ROOT = Path("/")
BOOT = Path("/boot")
DATA = Path("/home")
CACHE = DATA / ".oko-update"
LOCK = Path("/run/lock/oko-update.lock")
TARGET = Path("/run/oko-update-target")

BOOT_DEVICE = Path("/dev/disk/by-label/OKO_BOOT")
DATA_DEVICE = Path("/dev/disk/by-partlabel/oko-data")
SLOT_DEVICES = {
    "a": Path("/dev/disk/by-partlabel/oko-root-a"),
    "b": Path("/dev/disk/by-partlabel/oko-root-b"),
}
SLOT_PARTLABELS = {"a": "oko-root-a", "b": "oko-root-b"}

GRUB_CONFIG = BOOT / "grub/grub.cfg"
BOOT_STATE = BOOT / "oko/state.json"
BOOT_PAYLOADS = {
    "/usr/lib/oko-update/boot/bzImage": "bzImage",
}
# Transitional compatibility: old update bundles may still carry the former
# transport-only initramfs. Never install it into a root slot.
LEGACY_BOOT_PAYLOADS = ("/usr/lib/oko-update/boot/initrd",)

ACCOUNT_FILES = (
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/gshadow",
    "/etc/subuid",
    "/etc/subgid",
)

PERSISTENT_PATHS = (
    "/etc/fstab",
    "/etc/crypttab",
    "/etc/hostname",
    "/etc/hosts",
    "/etc/machine-id",
    "/etc/localtime",
    "/etc/timezone",
    "/etc/resolv.conf",
    "/etc/systemd/network",
    "/etc/systemd/networkd.conf",
    "/etc/systemd/networkd.conf.d",
    "/etc/systemd/resolved.conf",
    "/etc/systemd/resolved.conf.d",
    "/etc/NetworkManager",
    "/etc/network",
    "/etc/netplan",
    "/etc/wpa_supplicant",
    "/etc/iwd",
    "/var/lib/iwd",
    "/etc/wireguard",
    "/etc/udev/rules.d/70-persistent-net.rules",
    "/etc/ssh/sshd_config",
    "/etc/ssh/sshd_config.d",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/var/lib/systemd/linger",
    "/var/spool/cron",
)

EXCLUDED_ROOTFS_CONTENT = ("boot", "dev", "home", "media", "mnt", "proc", "run", "sys", "tmp")

VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
MAX_METADATA = 16 * 1024
MAX_PAYLOAD = 32 * 1024**3
MAX_ARCHIVE_ENTRIES = 2_000_000
MAX_UNPACKED = 128 * 1024**3
MAX_CAPTURE = 2 * 1024**2
MAX_BOOT_FILE = 1024**3
DOWNLOAD_CHUNK = 1024**2
DOWNLOAD_REPORT_STEP = 64 * 1024**2
FREE_RESERVE = 256 * 1024**2


class UpdateError(RuntimeError):
    """Expected update failure that is safe to display to the operator."""


@dataclass(frozen=True)
class Settings:
    endpoint: str
    kernel_args: str


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str | None


@dataclass(frozen=True)
class SlotLayout:
    active: str
    inactive: str
    active_device: Path
    inactive_device: Path


@dataclass
class ArchiveInfo:
    paths: set[str]
    captured: dict[str, bytes]
    boot_hashes: dict[str, str]
    entries: int
    unpacked_bytes: int


def info(message: str) -> None:
    print(f"oko-update: {message}", flush=True)


def warning(message: str) -> None:
    print(f"oko-update: warning: {message}", file=sys.stderr, flush=True)


def fail(message: str) -> NoReturn:
    raise UpdateError(message)


def target_path(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/"):
        raise ValueError(f"target path is not absolute: {absolute}")
    return root / absolute.lstrip("/")


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        loaded = parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as error:
        fail(f"cannot read {path}: {error}")
    if not loaded or "update" not in parser:
        fail(f"missing [update] configuration in {path}")
    section = parser["update"]
    endpoint = section.get("endpoint", DEFAULT_ENDPOINT).strip()
    kernel_args = " ".join(
        section.get("kernel_args", DEFAULT_KERNEL_ARGS).split()
    )
    if not endpoint or not kernel_args:
        fail(f"invalid empty setting in {path}")
    if any(character in kernel_args for character in "\r\n"):
        fail("kernel_args contains a newline")
    forbidden = (
        token
        for token in kernel_args.split()
        if token.startswith(("root=", "oko.slot=", "BOOT_IMAGE=", "initrd="))
    )
    if next(forbidden, None) is not None:
        fail("kernel_args must not set root=, oko.slot=, BOOT_IMAGE= or initrd=")
    return Settings(endpoint, kernel_args)


def validate_https_url(url: str, *, payload: bool = False) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        fail(f"only HTTPS update URLs are accepted: {url}")
    if parsed.username or parsed.password or parsed.fragment:
        fail(f"invalid update URL: {url}")
    if payload and not parsed.path.endswith(".tar.zst"):
        fail("update payload URL must end in .tar.zst")
    return url


def read_limited(source: BinaryIO, limit: int) -> bytes:
    data = source.read(limit + 1)
    if len(data) > limit:
        fail(f"server response exceeds {limit} bytes")
    return data


def parse_release_metadata(text: str) -> Release:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) not in (2, 3):
        fail("latest endpoint must contain version, URL, and optional SHA-256")
    version, url = lines[:2]
    if not VERSION_RE.fullmatch(version):
        fail(f"invalid release version: {version!r}")
    validate_https_url(url, payload=True)
    digest = None
    if len(lines) == 3:
        digest = lines[2]
        for prefix in ("sha256:", "sha256=", "SHA256:", "SHA256="):
            if digest.startswith(prefix):
                digest = digest[len(prefix) :].strip()
                break
        if not SHA256_RE.fullmatch(digest):
            fail("third endpoint line is not a valid SHA-256 digest")
        digest = digest.lower()
    return Release(version, url, digest)


def fetch_release(settings: Settings) -> Release:
    validate_https_url(settings.endpoint)
    request = urllib.request.Request(
        settings.endpoint,
        headers={"Accept": "text/plain", "User-Agent": "oko-update/1"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=ssl.create_default_context()
        ) as response:
            validate_https_url(response.geturl())
            if response.status != 200:
                fail(f"latest endpoint returned HTTP {response.status}")
            raw = read_limited(response, MAX_METADATA)
    except UpdateError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        fail(f"cannot fetch update metadata: {error}")
    try:
        return parse_release_metadata(raw.decode("utf-8"))
    except UnicodeDecodeError:
        fail("latest endpoint is not UTF-8 text")


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = re.sub(r"\\([\\\"$`])", r"\1", value)
        values[key.strip()] = value
    return values


def os_version(root: Path = ROOT) -> str:
    for absolute in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            values = parse_os_release(
                target_path(root, absolute).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as error:
            fail(f"cannot read {absolute}: {error}")
        if values.get("VERSION_ID"):
            return values["VERSION_ID"]
    fail("VERSION_ID is missing from os-release")


def acquire_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        fail("another oko-update process is already running")
    return handle


def require_root_and_commands() -> None:
    if os.geteuid() != 0:
        fail("installation must be run as root")
    missing = [
        command
        for command in ("mkfs.ext4", "mount", "umount", "tar", "zstd", "systemctl")
        if shutil.which(command) is None
    ]
    if missing:
        fail("required commands are missing: " + ", ".join(missing))


def block_device_id(path: Path) -> int:
    try:
        status = path.resolve(strict=True).stat()
    except OSError as error:
        fail(f"required device {path} is unavailable: {error}")
    if not stat.S_ISBLK(status.st_mode):
        fail(f"{path} is not a block device")
    return status.st_rdev


def parent_disk_id(path: Path) -> int:
    """Return the kernel device id of the physical parent for a partition."""

    device_id = block_device_id(path)
    sysfs_link = Path("/sys/dev/block") / (
        f"{os.major(device_id)}:{os.minor(device_id)}"
    )
    try:
        sysfs_device = sysfs_link.resolve(strict=True)
        if not (sysfs_device / "partition").is_file():
            fail(f"{path} is not a partition")
        major_text, minor_text = (
            (sysfs_device.parent / "dev")
            .read_text(encoding="ascii")
            .strip()
            .split(":", 1)
        )
        return os.makedev(int(major_text), int(minor_text))
    except (OSError, ValueError) as error:
        fail(f"cannot identify the parent disk for {path}: {error}")


def mounted_device_ids() -> set[int]:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot read mount table: {error}")
    result: set[int] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or ":" not in fields[2]:
            continue
        major, minor = fields[2].split(":", 1)
        try:
            result.add(os.makedev(int(major), int(minor)))
        except ValueError:
            continue
    return result


def cmdline_slot() -> str | None:
    try:
        tokens = Path("/proc/cmdline").read_text(encoding="utf-8").split()
    except OSError as error:
        fail(f"cannot read kernel command line: {error}")
    values = [token.split("=", 1)[1].lower() for token in tokens if token.startswith("oko.slot=")]
    if not values:
        return None
    if len(values) != 1 or values[0] not in SLOT_DEVICES:
        fail("kernel command line contains an invalid oko.slot value")
    return values[0]


def inspect_layout() -> SlotLayout:
    device_ids = {slot: block_device_id(path) for slot, path in SLOT_DEVICES.items()}
    if device_ids["a"] == device_ids["b"]:
        fail("root A and B resolve to the same block device")

    layout_devices = {
        "root A": SLOT_DEVICES["a"],
        "root B": SLOT_DEVICES["b"],
        "boot": BOOT_DEVICE,
        "data": DATA_DEVICE,
    }
    if len({block_device_id(path) for path in layout_devices.values()}) != len(
        layout_devices
    ):
        fail("OKO boot, root, and data labels do not resolve to distinct partitions")
    parent_disks = {
        name: parent_disk_id(path) for name, path in layout_devices.items()
    }
    if len(set(parent_disks.values())) != 1:
        fail("OKO boot, root, and data partitions do not belong to one disk")

    root_id = ROOT.stat().st_dev
    matching = [slot for slot, device_id in device_ids.items() if device_id == root_id]
    declared = cmdline_slot()
    if declared is not None:
        if device_ids[declared] != root_id:
            fail(f"oko.slot={declared} does not match the mounted root device")
        active = declared
    elif len(matching) == 1:
        active = matching[0]
    else:
        fail("cannot identify the active OKO root slot")

    inactive = "b" if active == "a" else "a"
    if device_ids[inactive] in mounted_device_ids():
        fail(f"inactive root slot {inactive.upper()} is mounted; refusing to overwrite it")

    boot_id = block_device_id(BOOT_DEVICE)
    data_id = block_device_id(DATA_DEVICE)
    try:
        if BOOT.stat().st_dev != boot_id:
            fail(f"{BOOT} is not the OKO_BOOT filesystem")
        if DATA.stat().st_dev != data_id:
            fail(f"{DATA} is not the oko-data partition")
    except OSError as error:
        fail(f"cannot inspect required mounts: {error}")
    if ROOT.stat().st_dev == DATA.stat().st_dev:
        fail("/home is not a separate persistent filesystem")

    return SlotLayout(
        active,
        inactive,
        SLOT_DEVICES[active].resolve(strict=True),
        SLOT_DEVICES[inactive].resolve(strict=True),
    )


def ensure_cache() -> None:
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    status = CACHE.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != 0:
        fail(f"unsafe update cache directory: {CACHE}")
    os.chmod(CACHE, 0o700)


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def download_release(release: Release) -> tuple[Path, str]:
    validate_https_url(release.url, payload=True)
    destination = CACHE / f"oko-{release.version}.tar.zst"
    partial = CACHE / f".{destination.name}.part"
    partial.unlink(missing_ok=True)
    free = shutil.disk_usage(CACHE).free
    digest = hashlib.sha256()
    written = 0
    next_report = DOWNLOAD_REPORT_STEP
    request = urllib.request.Request(
        release.url,
        headers={"Accept": "application/octet-stream", "User-Agent": "oko-update/1"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=60, context=ssl.create_default_context()
        ) as response:
            validate_https_url(response.geturl(), payload=True)
            if response.status != 200:
                fail(f"payload endpoint returned HTTP {response.status}")
            header = response.headers.get("Content-Length")
            try:
                expected = int(header) if header else None
            except ValueError:
                fail("payload has an invalid Content-Length")
            if expected is not None and (
                expected > MAX_PAYLOAD or expected + FREE_RESERVE > free
            ):
                fail("payload is too large for the persistent update cache")
            with partial.open("xb") as output:
                os.chmod(partial, 0o600)
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_PAYLOAD or written + FREE_RESERVE > free:
                        fail("payload is too large for the persistent update cache")
                    output.write(chunk)
                    digest.update(chunk)
                    if written >= next_report:
                        info(f"downloaded {format_bytes(written)}")
                        next_report += DOWNLOAD_REPORT_STEP
                output.flush()
                os.fsync(output.fileno())
            if expected is not None and written != expected:
                fail(f"short download: expected {expected} bytes, received {written}")
        os.replace(partial, destination)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        partial.unlink(missing_ok=True)
        fail(f"cannot download rootfs payload: {error}")

    actual = digest.hexdigest()
    if release.sha256 is not None and actual != release.sha256:
        destination.unlink(missing_ok=True)
        fail(f"payload SHA-256 mismatch: expected {release.sha256}, got {actual}")
    info(f"download complete: {format_bytes(written)}, SHA-256 {actual}")
    return destination, actual


def normalize_archive_path(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute():
        fail(f"archive contains an absolute path: {name!r}")
    parts = [part for part in path.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        fail(f"archive path escapes root: {name!r}")
    return "/" + "/".join(parts) if parts else "/"


def normalize_link(path: str, linkname: str, *, hardlink: bool) -> str:
    if not linkname:
        fail(f"archive contains an empty link at {path}")
    if hardlink:
        if linkname.startswith("/"):
            fail(f"archive hardlink has an absolute target: {path} -> {linkname}")
        return normalize_archive_path(linkname)
    target = (
        posixpath.normpath(linkname)
        if linkname.startswith("/")
        else posixpath.normpath(posixpath.join(posixpath.dirname(path), linkname))
    )
    if not target.startswith("/") or target == "/.." or target.startswith("/../"):
        fail(f"archive symlink escapes root: {path} -> {linkname}")
    return target


def add_path_and_parents(paths: set[str], path: str) -> None:
    while path != "/":
        paths.add(path)
        path = posixpath.dirname(path)


def copy_member(member: tarfile.TarInfo, archive: tarfile.TarFile, destination: Path) -> str:
    if member.size <= 0 or member.size > MAX_BOOT_FILE:
        fail(f"invalid boot payload size for {member.name}: {member.size}")
    source = archive.extractfile(member)
    if source is None:
        fail(f"cannot read boot payload {member.name}")
    digest = hashlib.sha256()
    with destination.open("xb") as output:
        while True:
            chunk = source.read(DOWNLOAD_CHUNK)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(destination, 0o600)
    return digest.hexdigest()


def inspect_archive(payload: Path, staging: Path) -> ArchiveInfo:
    capture_names = set(ACCOUNT_FILES) | {"/etc/os-release", "/usr/lib/os-release"}
    paths: set[str] = set()
    captured: dict[str, bytes] = {}
    boot_hashes: dict[str, str] = {}
    symlinks: set[str] = set()
    hardlinks: list[tuple[str, str]] = []
    entries = 0
    unpacked = 0
    decoder = subprocess.Popen(
        ["zstd", "-dc", "--", str(payload)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert decoder.stdout is not None and decoder.stderr is not None
    try:
        with tarfile.open(fileobj=decoder.stdout, mode="r|") as archive:
            for member in archive:
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES:
                    fail("rootfs archive contains too many entries")
                path = normalize_archive_path(member.name)
                add_path_and_parents(paths, path)
                if member.issym():
                    normalize_link(path, member.linkname, hardlink=False)
                    symlinks.add(path)
                elif member.islnk():
                    target = normalize_link(path, member.linkname, hardlink=True)
                    hardlinks.append((path, target))
                elif member.isfile():
                    if member.size < 0:
                        fail(f"archive contains a negative file size at {path}")
                    unpacked += member.size
                    if unpacked > MAX_UNPACKED:
                        fail("uncompressed rootfs exceeds the 128 GiB safety limit")
                    if path in BOOT_PAYLOADS:
                        if path in boot_hashes:
                            fail(f"duplicate boot payload in archive: {path}")
                        boot_hashes[path] = copy_member(
                            member, archive, staging / BOOT_PAYLOADS[path]
                        )
                    elif path in capture_names:
                        source = archive.extractfile(member)
                        if source is None:
                            fail(f"cannot read required archive file {path}")
                        data = source.read(MAX_CAPTURE + 1)
                        if len(data) > MAX_CAPTURE:
                            fail(f"archive metadata file is too large: {path}")
                        captured[path] = data
    except UpdateError:
        decoder.kill()
        decoder.wait()
        raise
    except (tarfile.TarError, OSError) as error:
        decoder.kill()
        decoder.wait()
        fail(f"cannot inspect rootfs archive: {error}")
    finally:
        decoder.stdout.close()

    decoder_error = decoder.stderr.read().decode("utf-8", errors="replace").strip()
    decoder_status = decoder.wait()
    if decoder_status:
        fail(f"cannot decompress rootfs archive: {decoder_error or decoder_status}")

    for path in paths:
        parent = posixpath.dirname(path)
        while parent != "/":
            if parent in symlinks:
                fail(f"archive writes through symlink {parent}: {path}")
            parent = posixpath.dirname(parent)
    for path, target in hardlinks:
        if target not in paths:
            fail(f"archive hardlink target is missing: {path} -> {target}")

    return ArchiveInfo(paths, captured, boot_hashes, entries, unpacked)


def archive_version(archive: ArchiveInfo) -> str:
    raw = archive.captured.get("/etc/os-release") or archive.captured.get(
        "/usr/lib/os-release"
    )
    if raw is None:
        fail("rootfs archive has no readable os-release")
    try:
        version = parse_os_release(raw.decode("utf-8")).get("VERSION_ID")
    except UnicodeDecodeError:
        fail("rootfs os-release is not UTF-8")
    if not version:
        fail("rootfs archive has no VERSION_ID")
    return version


def validate_archive(archive: ArchiveInfo, release: Release) -> None:
    actual = archive_version(archive)
    if actual != release.version:
        fail(f"endpoint announces {release.version}, but payload contains {actual}")
    required_paths = (
        "/etc/passwd",
        "/etc/shadow",
        "/etc/group",
        "/etc/gshadow",
        "/usr/sbin/oko-update",
    )
    missing = [path for path in required_paths if path not in archive.paths]
    missing.extend(path for path in BOOT_PAYLOADS if path not in archive.boot_hashes)
    if missing:
        fail("rootfs archive is incomplete; missing " + ", ".join(missing))
    for description, alternatives in (
        ("Python", ("/usr/bin/python3", "/bin/python3")),
        ("systemctl", ("/usr/bin/systemctl", "/bin/systemctl")),
    ):
        if not any(path in archive.paths for path in alternatives):
            fail(f"rootfs archive contains no {description} executable")


def account_text(data: bytes | None, name: str, required: bool = False) -> str:
    if data is None:
        if required:
            fail(f"rootfs archive is missing {name}")
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        fail(f"account file is not UTF-8: {name}")


def read_accounts(root: Path = ROOT) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name in ACCOUNT_FILES:
        try:
            result[name] = target_path(root, name).read_bytes()
        except FileNotFoundError:
            result[name] = b""
        except OSError as error:
            fail(f"cannot read {name}: {error}")
    return result


def colon_rows(text: str, minimum_fields: int, filename: str) -> list[list[str]]:
    records: list[list[str]] = []
    for line in text.splitlines():
        if not line:
            continue
        fields = line.split(":")
        if len(fields) < minimum_fields or not fields[0]:
            fail(f"malformed entry in {filename}: {line!r}")
        records.append(fields)
    return records


def serialize_rows(records: Iterable[list[str]]) -> bytes:
    text = "\n".join(":".join(record) for record in records)
    return (text + "\n").encode("utf-8") if text else b""


def installed_uid_min(root: Path = ROOT) -> int:
    try:
        text = target_path(root, "/etc/login.defs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 1000
    match = re.search(r"(?m)^\s*UID_MIN\s+(\d+)\s*(?:#.*)?$", text)
    return int(match.group(1)) if match else 1000


def merge_named_rows(
    new_records: list[list[str]],
    old_records: list[list[str]],
    preserved_names: set[str],
) -> list[list[str]]:
    old_by_name = {
        record[0]: record for record in old_records if record[0] in preserved_names
    }
    result: list[list[str]] = []
    seen: set[str] = set()
    for record in new_records:
        if record[0] in old_by_name:
            result.append(old_by_name[record[0]])
            seen.add(record[0])
        else:
            result.append(record)
    result.extend(
        record
        for record in old_records
        if record[0] in preserved_names and record[0] not in seen
    )
    return result


def merge_range_file(new: str, old: str, users: set[str], filename: str) -> bytes:
    new_records = colon_rows(new, 3, filename) if new else []
    old_records = colon_rows(old, 3, filename) if old else []
    return serialize_rows(
        [record for record in new_records if record[0] not in users]
        + [record for record in old_records if record[0] in users]
    )


def merge_accounts(
    archive: ArchiveInfo,
    installed: dict[str, bytes],
    root: Path = ROOT,
) -> tuple[dict[str, bytes], set[str]]:
    def old(name: str) -> str:
        return account_text(installed.get(name), name)

    def new(name: str, required: bool = False) -> str:
        return account_text(archive.captured.get(name), name, required)

    old_passwd = colon_rows(old("/etc/passwd"), 7, "/etc/passwd")
    new_passwd = colon_rows(new("/etc/passwd", True), 7, "/etc/passwd")
    uid_min = installed_uid_min(root)
    users = {
        record[0]
        for record in old_passwd
        if uid_min <= int(record[2]) < 65534
        or record[5] == "/home"
        or record[5].startswith("/home/")
    }
    old_users = {record[0]: record for record in old_passwd if record[0] in users}
    new_by_name = {record[0]: record for record in new_passwd}
    new_by_uid = {int(record[2]): record[0] for record in new_passwd}
    for name, record in old_users.items():
        user_id = int(record[2])
        if name in new_by_name and int(new_by_name[name][2]) != user_id:
            fail(f"user {name} changes UID in the new release")
        owner = new_by_uid.get(user_id)
        if owner is not None and owner != name:
            fail(f"UID {user_id} for {name} is used by {owner} in the new release")
    merged_passwd = merge_named_rows(new_passwd, old_passwd, users)

    old_shadow = colon_rows(old("/etc/shadow"), 2, "/etc/shadow")
    new_shadow = colon_rows(new("/etc/shadow", True), 2, "/etc/shadow")
    merged_shadow = merge_named_rows(new_shadow, old_shadow, users | {"root"})

    old_group = colon_rows(old("/etc/group"), 4, "/etc/group")
    new_group = colon_rows(new("/etc/group", True), 4, "/etc/group")
    primary_gids = {int(record[3]) for record in old_users.values()}
    preserved_groups = {
        record[0]
        for record in old_group
        if int(record[2]) in primary_gids
        or users.intersection(filter(None, record[3].split(",")))
    }
    old_groups = {
        record[0]: record for record in old_group if record[0] in preserved_groups
    }
    new_group_by_name = {record[0]: record for record in new_group}
    new_group_by_gid = {int(record[2]): record[0] for record in new_group}
    for name, record in old_groups.items():
        group_id = int(record[2])
        if name in new_group_by_name and int(new_group_by_name[name][2]) != group_id:
            fail(f"group {name} changes GID in the new release")
        owner = new_group_by_gid.get(group_id)
        if owner is not None and owner != name:
            fail(f"GID {group_id} for {name} is used by {owner} in the new release")

    merged_group: list[list[str]] = []
    seen_groups: set[str] = set()
    for record in new_group:
        old_record = old_groups.get(record[0])
        if old_record is None:
            merged_group.append(record)
            continue
        members = list(filter(None, record[3].split(",")))
        for member in filter(None, old_record[3].split(",")):
            if member not in members:
                members.append(member)
        merged_group.append(record[:3] + [",".join(members)])
        seen_groups.add(record[0])
    merged_group.extend(
        record
        for record in old_group
        if record[0] in preserved_groups and record[0] not in seen_groups
    )

    old_gshadow = (
        colon_rows(old("/etc/gshadow"), 4, "/etc/gshadow")
        if old("/etc/gshadow")
        else []
    )
    new_gshadow = colon_rows(new("/etc/gshadow", True), 4, "/etc/gshadow")
    old_gshadow_by_name = {
        record[0]: record for record in old_gshadow if record[0] in preserved_groups
    }
    merged_gshadow: list[list[str]] = []
    seen_gshadow: set[str] = set()
    for record in new_gshadow:
        old_record = old_gshadow_by_name.get(record[0])
        if old_record is None:
            merged_gshadow.append(record)
            continue
        administrators = list(filter(None, record[2].split(",")))
        members = list(filter(None, record[3].split(",")))
        for administrator in filter(None, old_record[2].split(",")):
            if administrator not in administrators:
                administrators.append(administrator)
        for member in filter(None, old_record[3].split(",")):
            if member not in members:
                members.append(member)
        merged_gshadow.append(
            [record[0], old_record[1], ",".join(administrators), ",".join(members)]
        )
        seen_gshadow.add(record[0])
    for name in preserved_groups - seen_gshadow:
        if name in old_gshadow_by_name:
            merged_gshadow.append(old_gshadow_by_name[name])
        else:
            members = next((record[3] for record in old_group if record[0] == name), "")
            merged_gshadow.append([name, "!", "", members])

    return {
        "/etc/passwd": serialize_rows(merged_passwd),
        "/etc/shadow": serialize_rows(merged_shadow),
        "/etc/group": serialize_rows(merged_group),
        "/etc/gshadow": serialize_rows(merged_gshadow),
        "/etc/subuid": merge_range_file(
            new("/etc/subuid"), old("/etc/subuid"), users, "/etc/subuid"
        ),
        "/etc/subgid": merge_range_file(
            new("/etc/subgid"), old("/etc/subgid"), users, "/etc/subgid"
        ),
    }, users


def run(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        fail(f"{description} failed: {detail[-2000:]}")
    return result


def mount_inactive(layout: SlotLayout) -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    if any(TARGET.iterdir()):
        fail(f"temporary mountpoint is not empty: {TARGET}")
    run(
        ["mount", "-o", "nodev,nosuid", str(layout.inactive_device), str(TARGET)],
        "mounting inactive root",
    )


def unmount_inactive(*, required: bool = False) -> None:
    result = subprocess.run(
        ["umount", str(TARGET)], text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = (
            result.stderr or result.stdout or "could not unmount inactive root"
        ).strip()
        if required:
            fail(detail)
        warning(detail)


def rootfs_exclusion_patterns() -> list[str]:
    patterns: list[str] = []
    for directory in EXCLUDED_ROOTFS_CONTENT:
        patterns.extend((f"{directory}/*", f"./{directory}/*"))
    # These are transport objects appended by oko-update-bundle. Their only
    # installed copies belong to the shared boot filesystem, not the root slot.
    for absolute in (*BOOT_PAYLOADS, *LEGACY_BOOT_PAYLOADS):
        relative = absolute.lstrip("/")
        patterns.extend((relative, f"./{relative}"))
    return patterns


def rootfs_tar_command(exclude_file: Path) -> list[str]:
    return [
        "tar",
        "--extract",
        "--file=-",
        f"--directory={TARGET}",
        "--numeric-owner",
        "--same-owner",
        "--preserve-permissions",
        "--delay-directory-restore",
        "--keep-directory-symlink",
        "--xattrs",
        "--acls",
        # GNU tar exclusion patterns otherwise match after every '/'. Anchoring
        # keeps each rule scoped to the archive root.
        "--anchored",
        f"--exclude-from={exclude_file}",
    ]


def extract_rootfs(payload: Path, work: Path) -> None:
    exclude_file = work / "rootfs.exclude"
    exclude_file.write_text(
        "\n".join(rootfs_exclusion_patterns()) + "\n", encoding="utf-8"
    )
    log_file = work / "extract.log"
    command = rootfs_tar_command(exclude_file)
    with log_file.open("wb") as log:
        decoder = subprocess.Popen(
            ["zstd", "-dc", "--", str(payload)],
            stdout=subprocess.PIPE,
            stderr=log,
        )
        assert decoder.stdout is not None
        extractor = subprocess.Popen(command, stdin=decoder.stdout, stdout=log, stderr=log)
        decoder.stdout.close()
        tar_status = extractor.wait()
        zstd_status = decoder.wait()
    if tar_status or zstd_status:
        try:
            detail = log_file.read_text(encoding="utf-8", errors="replace")[-8192:].strip()
        except OSError:
            detail = ""
        fail(f"rootfs extraction failed: {detail or (tar_status, zstd_status)}")


def remove_target_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_persistent_path(source: Path, destination: Path) -> None:
    remove_target_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = source.lstat().st_mode
    if stat.S_ISLNK(mode):
        destination.symlink_to(os.readlink(source))
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISDIR(mode):
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif stat.S_ISREG(mode):
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        fail(f"refusing to preserve unsupported file type: {source}")


def preserve_configuration() -> None:
    critical_directories = ("/etc", "/usr", "/var")
    target_root = TARGET.resolve()
    for absolute in critical_directories:
        path = target_path(TARGET, absolute)
        if path.is_symlink() or not path.is_dir():
            fail(f"new rootfs has an unsafe {absolute} directory")
    paths = [Path(absolute) for absolute in PERSISTENT_PATHS]
    ssh_directory = Path("/etc/ssh")
    if ssh_directory.is_dir():
        paths.extend(ssh_directory.glob("ssh_host_*"))
    for source in paths:
        if not source.exists() and not source.is_symlink():
            continue
        relative = source.relative_to("/")
        destination = TARGET / relative
        resolved_parent = destination.parent.resolve()
        if os.path.commonpath((target_root, resolved_parent)) != str(target_root):
            fail(f"unsafe destination while preserving {source}")
        copy_persistent_path(source, destination)


def rewrite_fstab(text: str, slot: str) -> str:
    output: list[str] = []
    found_root = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue
        fields = stripped.split()
        if len(fields) >= 2 and fields[1] == "/":
            fields[0] = f"PARTLABEL={SLOT_PARTLABELS[slot]}"
            if len(fields) >= 6:
                fields[5] = "1"
            line = "\t".join(fields)
            found_root = True
        output.append(line)
    if not found_root:
        output.append(
            f"PARTLABEL={SLOT_PARTLABELS[slot]}\t/\text4\tdefaults\t0\t1"
        )
    return "\n".join(output) + "\n"


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.oko-new-{os.getpid()}")
    try:
        with temporary.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        if str(path).startswith(str(TARGET) + "/"):
            os.chown(temporary, 0, 0)
        os.replace(temporary, path)
        try:
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def write_accounts(accounts: dict[str, bytes]) -> None:
    modes = {
        "/etc/passwd": 0o644,
        "/etc/shadow": 0o600,
        "/etc/group": 0o644,
        "/etc/gshadow": 0o600,
        "/etc/subuid": 0o644,
        "/etc/subgid": 0o644,
    }
    for absolute, data in accounts.items():
        atomic_write(target_path(TARGET, absolute), data, modes[absolute])


def enable_preserved_wireguard(root: Path = TARGET) -> None:
    """Enable the installer-managed tunnel using the new rootfs unit files."""

    configuration = target_path(root, "/etc/wireguard/wg0.conf")
    if not configuration.is_file():
        return
    unit_locations = (
        "/usr/lib/systemd/system/wg-quick@.service",
        "/lib/systemd/system/wg-quick@.service",
    )
    if not any(target_path(root, path).is_file() for path in unit_locations):
        fail("new rootfs has WireGuard configuration but no wg-quick@.service")
    run(
        [
            "systemctl",
            "--root",
            str(root),
            "enable",
            "wg-quick@wg0.service",
        ],
        "enabling preserved WireGuard tunnel",
    )


def configure_inactive_root(slot: str, accounts: dict[str, bytes], release: Release) -> None:
    for directory in ("/boot", "/dev", "/home", "/media", "/mnt", "/proc", "/run", "/sys", "/tmp"):
        target_path(TARGET, directory).mkdir(parents=True, exist_ok=True)
    preserve_configuration()
    write_accounts(accounts)
    enable_preserved_wireguard()
    fstab = target_path(TARGET, "/etc/fstab")
    try:
        fstab_text = fstab.read_text(encoding="utf-8")
    except FileNotFoundError:
        fstab_text = ""
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read new /etc/fstab: {error}")
    atomic_write(fstab, rewrite_fstab(fstab_text, slot).encode("utf-8"), 0o644)
    actual = os_version(TARGET)
    if actual != release.version:
        fail(f"inactive root VERSION_ID is {actual}, expected {release.version}")


def copy_boot_file(source: Path, destination: Path) -> str:
    temporary = destination.with_name(f".{destination.name}.oko-new-{os.getpid()}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            while True:
                chunk = input_file.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                output_file.write(chunk)
                digest.update(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()


def ensure_active_boot_kernel(layout: SlotLayout) -> None:
    try:
        if os.statvfs(BOOT).f_flag & os.ST_RDONLY:
            fail(f"{BOOT} is mounted read-only")
    except OSError as error:
        fail(f"cannot inspect {BOOT}: {error}")
    directory = BOOT / "oko/slots" / layout.active
    missing = [name for name in ("bzImage",) if not (directory / name).is_file()]
    if missing:
        fail(
            f"active boot slot {layout.active.upper()} is not initialized; missing "
            + ", ".join(str(directory / name) for name in missing)
        )
    if not GRUB_CONFIG.is_file():
        fail("shared boot partition does not contain /boot/grub/grub.cfg")


def install_boot_kernel(
    layout: SlotLayout,
    staging: Path,
    archive: ArchiveInfo,
    release: Release,
    payload_sha256: str,
) -> None:
    ensure_active_boot_kernel(layout)
    directory = BOOT / "oko/slots" / layout.inactive
    directory.mkdir(parents=True, exist_ok=True)
    installed_hashes = {
        "/usr/lib/oko-update/boot/bzImage": copy_boot_file(
            staging / "bzImage", directory / "bzImage"
        ),
    }
    if installed_hashes != archive.boot_hashes:
        fail("kernel verification failed after copying to OKO_BOOT")
    metadata = {
        "version": release.version,
        "payload_sha256": payload_sha256,
        "kernel_sha256": archive.boot_hashes["/usr/lib/oko-update/boot/bzImage"],
    }
    atomic_write(
        directory / "release.json",
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o644,
    )
    os.sync()


def slot_kernel_commandline(slot: str, settings: Settings) -> str:
    return (
        f"root=PARTLABEL={SLOT_PARTLABELS[slot]} oko.slot={slot} "
        f"{settings.kernel_args}"
    )


def grub_configuration(preferred: str, settings: Settings) -> str:
    fallback = "b" if preferred == "a" else "a"

    def entry(slot: str) -> str:
        return f"""menuentry 'OKO Server (slot {slot.upper()})' --id oko-{slot} {{
    search --no-floppy --label OKO_BOOT --set=oko_boot
    set root=$oko_boot
    linux /oko/slots/{slot}/bzImage {slot_kernel_commandline(slot, settings)}
}}
"""

    return f"""# Generated by oko-update. Local changes will be replaced.
set timeout=5
set default=0
set fallback=1

insmod part_gpt
insmod fat

{entry(preferred)}
{entry(fallback)}"""


def switch_boot_slot(
    layout: SlotLayout,
    settings: Settings,
    release: Release,
    payload_sha256: str,
) -> None:
    state = {
        "preferred_slot": layout.inactive,
        "previous_slot": layout.active,
        "version": release.version,
        "payload_sha256": payload_sha256,
        "status": "pending-reboot",
    }
    atomic_write(
        BOOT_STATE,
        (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o644,
    )
    # Either old or new defaults are safe here: both complete slot payloads
    # already exist. Switch the one GRUB configuration shared by BIOS and UEFI
    # only after every other update step has completed.
    atomic_write(
        GRUB_CONFIG,
        grub_configuration(layout.inactive, settings).encode("utf-8"),
        0o644,
    )
    os.sync()


def confirm(current: str, release: Release, layout: SlotLayout) -> bool:
    print()
    print(f"Current version: {current} (slot {layout.active.upper()})")
    print(f"New version:     {release.version} (slot {layout.inactive.upper()})")
    print("The inactive root and boot files will be replaced.")
    print("The active slot and oko-data will not be modified.")
    try:
        return input("Install and reboot? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def install_update(
    settings: Settings,
    release: Release,
    current: str,
    *,
    assume_yes: bool,
    no_reboot: bool,
) -> None:
    require_root_and_commands()
    layout = inspect_layout()
    ensure_cache()
    ensure_active_boot_kernel(layout)
    if release.sha256 is None:
        warning(
            "the endpoint supplied no SHA-256; HTTPS and archive validation are "
            "not a substitute for a signed release manifest"
        )

    info(f"downloading OKO {release.version}")
    payload, payload_sha256 = download_release(release)
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=CACHE))
    mounted = False
    try:
        info("validating rootfs and kernel")
        archive = inspect_archive(payload, work)
        validate_archive(archive, release)
        installed_accounts = read_accounts()
        merged_accounts, users = merge_accounts(archive, installed_accounts)
        info(
            f"validated {archive.entries} entries ({format_bytes(archive.unpacked_bytes)}); "
            f"preserving {len(users)} human user(s)"
        )
        if not assume_yes and not confirm(current, release, layout):
            info(f"cancelled; downloaded payload remains at {payload}")
            return

        # Repeat the device check immediately before the destructive operation.
        current_layout = inspect_layout()
        if current_layout != layout:
            fail("storage layout changed during update preparation")

        info(f"formatting inactive root slot {layout.inactive.upper()}")
        run(
            [
                "mkfs.ext4",
                "-F",
                "-L",
                SLOT_PARTLABELS[layout.inactive],
                str(layout.inactive_device),
            ],
            "formatting inactive root",
        )
        mount_inactive(layout)
        mounted = True
        if archive.unpacked_bytes + FREE_RESERVE > shutil.disk_usage(TARGET).free:
            fail("inactive root partition is too small for this release")
        info("extracting the new rootfs")
        extract_rootfs(payload, work)
        info("preserving machine configuration and local users")
        configure_inactive_root(layout.inactive, merged_accounts, release)
        os.sync()
        unmount_inactive(required=True)
        mounted = False

        info(f"installing kernel for slot {layout.inactive.upper()}")
        install_boot_kernel(layout, work, archive, release, payload_sha256)
        info(f"making slot {layout.inactive.upper()} the preferred boot slot")
        switch_boot_slot(layout, settings, release, payload_sha256)
    finally:
        if mounted:
            unmount_inactive()
        shutil.rmtree(work, ignore_errors=True)

    payload.unlink(missing_ok=True)
    info(f"OKO {release.version} installed successfully")
    if no_reboot:
        warning("reboot disabled; the current slot remains running until you reboot")
        return
    info("rebooting")
    result = subprocess.run(["systemctl", "reboot"], check=False)
    if result.returncode:
        fail("update succeeded, but systemctl reboot failed; reboot manually")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install an OKO release into the inactive A/B slot"
    )
    parser.add_argument("--check", action="store_true", help="only check for an update")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--force", action="store_true", help="reinstall even if VERSION_ID already matches"
    )
    parser.add_argument(
        "--no-reboot", action="store_true", help="do not reboot after a successful update"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        settings = load_settings()
        current = os_version()
        release = fetch_release(settings)
        info(f"current version: {current}")
        info(f"latest version:  {release.version}")
        if current == release.version and not arguments.force:
            info("system is up to date")
            return 0
        if arguments.check:
            info("an update is available")
            return 0
        with acquire_lock():
            install_update(
                settings,
                release,
                current,
                assume_yes=arguments.yes,
                no_reboot=arguments.no_reboot,
            )
        return 0
    except KeyboardInterrupt:
        print("\noko-update: cancelled", file=sys.stderr)
        return 130
    except UpdateError as error:
        print(f"oko-update: error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"oko-update: unexpected error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

