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

"""Three-step OKO installer with persistent oko-data handling."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Callable, ClassVar, Sequence


VENDORED_SITE = Path("/usr/share/oko-python/site-packages")
if VENDORED_SITE.is_dir():
    sys.path.insert(0, str(VENDORED_SITE))

REQUIRED_TEXTUAL_VERSION = "8.2.8"
try:
    TEXTUAL_VERSION = package_version("textual")
except PackageNotFoundError:
    print(
        f"error: OKO Installer requires textual=={REQUIRED_TEXTUAL_VERSION}",
        file=sys.stderr,
    )
    raise SystemExit(2)

if TEXTUAL_VERSION != REQUIRED_TEXTUAL_VERSION:
    print(
        "error: OKO Installer requires "
        f"textual=={REQUIRED_TEXTUAL_VERSION}; found textual=={TEXTUAL_VERSION}",
        file=sys.stderr,
    )
    raise SystemExit(2)

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Key, Resize
from textual.widgets import Input, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

TARGET = Path("/run/oko-installer/target")
WORK_ROOT = Path("/run/oko-installer")
INSTALL_MEDIA_LABEL = "OKO_INSTALL"
BUNDLE_RELATIVE = Path("install/oko-rootfs.tar.zst")
BUNDLE_SHA256_RELATIVE = Path("install/oko-rootfs.tar.zst.sha256")
EFI_LOADER_RELATIVES = (
    Path("install/BOOTX64.EFI"),

    Path("boot/EFI/BOOT/bootx64.efi"),
    Path("boot/EFI/BOOT/BOOTX64.EFI"),

    # old ISO layout; can eventually be removed
    Path("EFI/BOOT/bootx64.efi"),
    Path("EFI/BOOT/BOOTX64.EFI"),
)

SECTOR_BYTES = 512
ALIGNMENT_SECTORS = 2048
BIOS_PARTITION_BYTES = 2 * 1024**2
BOOT_PARTITION_BYTES = 1024**3
ROOT_PARTITION_BYTES = 24 * 1024**3
MINIMUM_DATA_BYTES = 256 * 1024**2
MINIMUM_DISK_BYTES = 50 * 1024**3
BIOS_LABEL = "oko-bios"
BOOT_LABEL = "OKO_BOOT"
BOOT_PARTLABEL = "oko-boot"
ROOT_A_LABEL = "oko-root-a"
ROOT_B_LABEL = "oko-root-b"
DATA_LABEL = "oko-data"

# GPT partition type GUIDs. These replace gdisk/sgdisk's short type codes:
# EF02 = BIOS boot, EF00 = EFI System Partition, 8300 = Linux filesystem.
GPT_TYPE_BIOS_BOOT = "21686148-6449-6E6F-744E-656564454649"
GPT_TYPE_EFI_SYSTEM = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
GPT_TYPE_LINUX_FILESYSTEM = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"

# Keep the kernel's partition view unchanged until refresh_partitions() runs.
# --lock avoids races with udev while each on-disk GPT update is committed.
SFDISK_WRITE_OPTIONS = ("--lock", "--no-reread", "--no-tell-kernel")

DATA_MOUNTPOINT = "/home"
ADMIN_UID = 1000
# Force username to admin, because we has oko-proxy services with hardcoded paths /home/admin
ADMIN_USER = "admin"
INSTALLER_VERSION = "5.0.0"
WIREGUARD_INTERFACE = "wg0"
WIREGUARD_TEST_INTERFACE = "okowgtest"
WIREGUARD_KEEPALIVE = 25
WIREGUARD_TEST_TIMEOUT = 15
WIREGUARD_ALLOWED_IPS = "0.0.0.0/0"
KERNEL_ARGS = (
    "rw rootwait roottimeout=30 rootfstype=ext4 "
    "console=tty0 console=ttyS0,115200n8"
)
BOOT_TRANSPORT = {
    "/usr/lib/oko-update/boot/bzImage": "bzImage",
}
# Transitional compatibility: old update bundles may still carry the former
# transport-only initramfs. Never install it into a root slot.
LEGACY_BOOT_TRANSPORT_PATHS = ("/usr/lib/oko-update/boot/initrd",)
ROOTFS_EXCLUDES = (
    "boot",
    "dev",
    "home",
    "media",
    "mnt",
    "proc",
    "run",
    "sys",
    "tmp",
)
MAX_ARCHIVE_ENTRIES = 2_000_000
MAX_UNPACKED_BYTES = 128 * 1024**3
MAX_METADATA_BYTES = 2 * 1024**2
MAX_BOOT_FILE_BYTES = 1024**3
COPY_CHUNK = 1024**2
WIZARD_STEPS = ("Storage", "Server", "Network")
PAGE_TITLES = (
    "Choose the installation disk",
    "Set up this server",
    "Configure the installed network",
)
PAGE_DESCRIPTIONS = (
    "Select the disk that will receive OKO's A/B system layout. The live medium and mounted system disks are excluded automatically.",
    "Choose the server name, administrator password, and timezone. The administrator account is always admin; the real root account remains locked.",
    "This systemd-networkd profile is written into the installed system and takes effect on its first boot.",
)
FALLBACK_TIMEZONES = [
    ("UTC", "Coordinated Universal Time"),
    ("America/New_York", "Eastern Time"),
    ("America/Los_Angeles", "Pacific Time"),
    ("Europe/London", "United Kingdom"),
    ("Europe/Paris", "Central European Time"),
    ("Europe/Warsaw", "Central European Time"),
    ("Asia/Singapore", "Singapore"),
    ("Asia/Tokyo", "Japan"),
    ("Australia/Sydney", "New South Wales"),
]

# The rail deliberately stays at three conceptual phases. Each phase is split
# into small, keyboard-first screens so a first-time user never has to discover
# Tab navigation or understand a dense form.
STEP_PHASE = {
    "disk": 0,
    "data-policy": 0,
    "disk-confirm": 0,
    "hostname": 1,
    "password": 1,
    "password-confirm": 1,
    "timezone-region": 1,
    "timezone-city": 1,
    "interface": 2,
    "network-mode": 2,
    "address": 2,
    "gateway": 2,
    "dns": 2,
    "wireguard-choice": 2,
    "wireguard-private-key": 2,
    "wireguard-public": 2,
    "wireguard-peer-key": 2,
    "wireguard-endpoint": 2,
    "wireguard-address": 2,
    "wireguard-test": 2,
    "review": 2,
}
STEP_POSITION = {
    "disk": (1, 3),
    "data-policy": (2, 3),
    "disk-confirm": (3, 3),
    "hostname": (1, 4),
    "password": (2, 4),
    "password-confirm": (3, 4),
    "timezone-region": (4, 4),
    "timezone-city": (4, 4),
    "interface": (1, 12),
    "network-mode": (2, 12),
    "address": (3, 12),
    "gateway": (4, 12),
    "dns": (5, 12),
    "wireguard-choice": (6, 12),
    "wireguard-private-key": (7, 12),
    "wireguard-public": (8, 12),
    "wireguard-peer-key": (9, 12),
    "wireguard-endpoint": (10, 12),
    "wireguard-address": (11, 12),
    "wireguard-test": (12, 12),
}
HOSTNAME_RE = re.compile(r"^(?=.{1,63}$)[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$")
REQUIRED_COMMANDS = (
    "chroot",
    "findmnt",
    "grub-install",
    "ip",
    "lsblk",
    "mke2fs",
    "mkfs.vfat",
    "mount",
    "partprobe",
    "sfdisk",
    "swapoff",
    "tar",
    "udevadm",
    "umount",
    "wg",
    "zstd",
)
GRUB_PC_LIBRARY_ROOTS = (
    Path("/usr/lib"),
    Path("/usr/lib64"),
    Path("/lib"),
    Path("/lib64"),
)


class InstallError(RuntimeError):
    """An expected installation failure suitable for display to the user."""


def find_grub_pc_directory(
    library_roots: Sequence[Path] = GRUB_PC_LIBRARY_ROOTS,
) -> Path:
    """Locate the packaged BIOS GRUB modules on lib and lib64 systems."""

    for root in library_roots:
        directory = root / "grub/i386-pc"
        if (directory / "modinfo.sh").is_file():
            return directory
    searched = ", ".join(str(root / "grub/i386-pc") for root in library_roots)
    raise InstallError(
        "BIOS GRUB modules are missing from the installer image "
        f"(searched: {searched})"
    )


def find_install_media(relative: Path = BUNDLE_RELATIVE) -> Path:
    """Return the mounted ISO path containing the requested installer file."""

    label_link = Path("/dev/disk/by-label") / INSTALL_MEDIA_LABEL
    try:
        device = label_link.resolve(strict=True)
    except OSError as exc:
        raise InstallError(
            f"Installation media with label {INSTALL_MEDIA_LABEL} was not found"
        ) from exc

    targets: list[Path] = []
    for source in (label_link, device):
        try:
            result = subprocess.run(
                ["findmnt", "-rn", "-S", str(source), "-o", "TARGET"],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise InstallError(f"Cannot inspect installer-media mounts: {exc}") from exc
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout).strip()
            raise InstallError(
                f"Cannot inspect installer-media mounts: {detail or result.returncode}"
            )
        targets.extend(
            Path(line.strip()) for line in result.stdout.splitlines() if line.strip()
        )

    for target in dict.fromkeys(targets):
        if (target / relative).is_file():
            return target
    if targets:
        locations = ", ".join(str(path) for path in dict.fromkeys(targets))
        raise InstallError(
            f"Installer payload {relative} is missing from mounted media at {locations}"
        )
    raise InstallError(
        f"Installation media {device} is not mounted; wait for udev and try again"
    )


@dataclass(frozen=True)
class Disk:
    path: str
    model: str
    transport: str
    size: int
    logical_sector: int

    @property
    def size_text(self) -> str:
        value = float(self.size)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if value < 1024 or unit == "TiB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TiB"


@dataclass(frozen=True)
class Interface:
    name: str
    mac: str
    state: str


@dataclass(frozen=True)
class PartitionInfo:
    path: str
    number: int
    start_sector: int
    end_sector: int
    fstype: str

    @property
    def size(self) -> int:
        return (self.end_sector - self.start_sector + 1) * SECTOR_BYTES

    @property
    def size_text(self) -> str:
        value = float(self.size)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if value < 1024 or unit == "TiB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TiB"


@dataclass(frozen=True)
class BundleInfo:
    version: str
    boot_hashes: dict[str, str]
    entries: int
    unpacked_bytes: int


@dataclass
class InstallConfig:
    disk: Disk | None = None
    data_policy: str = "create"
    existing_data: PartitionInfo | None = None
    hostname: str = "oko-server"
    password: str = ""
    timezone: str = "UTC"
    interface: Interface | None = None
    network_mode: str = "dhcp"
    address: str = ""
    gateway: str = ""
    dns: str = "1.1.1.1 9.9.9.9"
    wireguard_enabled: bool = False
    wireguard_key_source: str = "generate"
    wireguard_private_key: str = ""
    wireguard_public_key: str = ""
    wireguard_peer_public_key: str = ""
    wireguard_endpoint: str = ""
    wireguard_address: str = ""
    wireguard_tested: bool = False


def flatten_lsblk(nodes: list[dict]) -> list[dict]:
    result: list[dict] = []
    for node in nodes:
        result.append(node)
        result.extend(flatten_lsblk(node.get("children") or []))
    return result


def inspect_disk_partitions(disk_path: str) -> list[dict]:
    """Return all lsblk nodes below one selected physical disk."""

    command = [
        "lsblk",
        "--json",
        "--bytes",
        "--output",
        "PATH,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL,PARTN,MOUNTPOINTS",
        disk_path,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        return flatten_lsblk(json.loads(result.stdout).get("blockdevices", []))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot inspect {disk_path}: {exc}") from exc


def partition_info(node: dict) -> PartitionInfo:
    """Read exact geometry; Linux exposes these values in 512-byte sectors."""

    path = str(node.get("path") or "")
    try:
        number = int(node.get("partn"))
        sysfs = Path("/sys/class/block") / Path(path).name
        start = int((sysfs / "start").read_text(encoding="ascii").strip())
        sectors = int((sysfs / "size").read_text(encoding="ascii").strip())
    except (OSError, TypeError, ValueError) as exc:
        device = path or "unknown device"
        raise InstallError(
            f"Cannot read partition geometry for {device}: {exc}"
        ) from exc
    if not path or number <= 0 or start < 0 or sectors <= 0:
        raise InstallError(f"Invalid partition geometry for {path or 'unknown device'}")
    return PartitionInfo(
        path=path,
        number=number,
        start_sector=start,
        end_sector=start + sectors - 1,
        fstype=str(node.get("fstype") or ""),
    )


def find_data_partition(disk_path: str) -> PartitionInfo | None:
    """Find the one partition identified as oko-data on a selected disk."""

    matches = []
    for node in inspect_disk_partitions(disk_path):
        if node.get("type") != "part":
            continue
        labels = {
            str(node.get("label") or ""),
            str(node.get("partlabel") or ""),
        }
        if DATA_LABEL in labels:
            matches.append(node)
    if len(matches) > 1:
        raise InstallError(
            f"More than one {DATA_LABEL!r} partition exists on {disk_path}; "
            "remove the ambiguity before installing"
        )
    return partition_info(matches[0]) if matches else None


def tree_has_mount(node: dict, protected_mounts: set[str]) -> bool:
    mounts = {item for item in (node.get("mountpoints") or []) if item}
    return bool(mounts & protected_mounts) or any(
        tree_has_mount(child, protected_mounts) for child in (node.get("children") or [])
    )


def install_media_parent_disk() -> str | None:
    """Return the physical disk that carries the labelled installer medium."""

    label_link = Path("/dev/disk/by-label") / INSTALL_MEDIA_LABEL
    try:
        media = label_link.resolve(strict=True)
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["lsblk", "-dnro", "TYPE,PKNAME", str(media)],
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = result.stdout.split()
    if not fields:
        return None
    if fields[0] == "disk":
        return str(media)
    if len(fields) >= 2 and fields[1]:
        return str(Path("/dev") / fields[1])
    return None


def discover_disks() -> list[Disk]:
    command = [
        "lsblk",
        "--json",
        "--bytes",
        "--output",
        "PATH,TYPE,SIZE,MODEL,TRAN,RO,MOUNTPOINTS,PKNAME,LOG-SEC",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot enumerate disks: {exc}") from exc

    roots = data.get("blockdevices", [])
    nodes = flatten_lsblk(roots)
    protected_mounts = {"/", "/run/live/medium", "/media/realroot"}
    protected_disks = {
        str(node.get("path", ""))
        for node in roots
        if node.get("type") == "disk" and tree_has_mount(node, protected_mounts)
    }
    media_disk = install_media_parent_disk()
    if media_disk:
        protected_disks.add(media_disk)

    disks: list[Disk] = []
    for node in nodes:
        path = str(node.get("path", ""))
        size = int(node.get("size") or 0)
        if (
            node.get("type") != "disk"
            or bool(node.get("ro"))
            or size < MINIMUM_DISK_BYTES
            or path in protected_disks
        ):
            continue
        disks.append(
            Disk(
                path=path,
                model=str(node.get("model") or "Unknown model").strip(),
                transport=str(node.get("tran") or "unknown").strip(),
                size=size,
                logical_sector=int(node.get("log-sec") or 512),
            )
        )
    return sorted(disks, key=lambda item: item.path)


def discover_interfaces() -> list[Interface]:
    interfaces: list[Interface] = []
    for path in sorted(Path("/sys/class/net").glob("*")):
        if path.name == "lo":
            continue
        interfaces.append(
            Interface(
                name=path.name,
                mac=(path / "address").read_text(encoding="ascii").strip(),
                state=(path / "operstate").read_text(encoding="ascii").strip(),
            )
        )
    return interfaces


def validate_server(hostname: str, password: str, confirm: str, timezone: str) -> str | None:
    if not HOSTNAME_RE.fullmatch(hostname):
        return "Server name must be a valid single DNS label (1-63 characters)."
    if len(password) < 8:
        return "Password must contain at least 8 characters."
    if password != confirm:
        return "The two passwords do not match."
    zone = Path("/usr/share/zoneinfo") / timezone
    if timezone.startswith("/") or ".." in Path(timezone).parts or not zone.is_file():
        return "Timezone is not present in /usr/share/zoneinfo (example: Europe/Paris)."
    return None


def parse_dns(value: str) -> list[str]:
    result: list[str] = []
    for entry in value.replace(",", " ").split():
        try:
            result.append(str(ipaddress.ip_address(entry)))
        except ValueError as exc:
            raise InstallError(f"Invalid DNS address: {entry}") from exc
    return result


def validate_network(mode: str, address: str, gateway: str, dns: str) -> str | None:
    if mode == "dhcp":
        return None
    if "/" not in address:
        return "Static address must include a prefix, for example 192.168.1.50/24."
    try:
        interface = ipaddress.ip_interface(address)
    except ValueError:
        return "Static address must include a prefix, for example 192.168.1.50/24."
    if gateway:
        try:
            gateway_ip = ipaddress.ip_address(gateway)
        except ValueError:
            return "Gateway is not a valid IP address."
        if gateway_ip.version != interface.ip.version:
            return "Static address and gateway must use the same IP version."
    try:
        parse_dns(dns)
    except InstallError as exc:
        return str(exc)
    return None


def validate_wireguard_key(value: str, description: str = "WireGuard key") -> str | None:
    if not value or any(character.isspace() for character in value):
        return f"{description} must be one base64 key without spaces."
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return f"{description} is not valid base64."
    if len(decoded) != 32:
        return f"{description} must decode to exactly 32 bytes."
    return None


def split_wireguard_endpoint(value: str) -> tuple[str, int]:
    """Parse the endpoint syntax accepted by wg: host:port or [IPv6]:port."""

    value = value.strip()
    if "://" in value:
        raise InstallError(
            "WireGuard endpoint must be host:port or [IPv6]:port, without a URL scheme."
        )
    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d+)", value)
        if not match:
            raise InstallError("Use [IPv6-address]:port for an IPv6 endpoint.")
        host, port_text = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise InstallError("The bracketed WireGuard endpoint is not valid IPv6.") from exc
    else:
        if value.count(":") != 1:
            raise InstallError("WireGuard endpoint must be host:port or [IPv6]:port.")
        host, port_text = value.rsplit(":", 1)
        if not host:
            raise InstallError("WireGuard endpoint host is empty.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            try:
                ascii_host = host.rstrip(".").encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise InstallError("WireGuard endpoint hostname is invalid.") from exc
            labels = ascii_host.split(".")
            if not ascii_host or len(ascii_host) > 253 or any(
                not HOSTNAME_RE.fullmatch(label) for label in labels
            ):
                raise InstallError("WireGuard endpoint hostname is invalid.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise InstallError("WireGuard endpoint port must be a number.") from exc
    if not 1 <= port <= 65535:
        raise InstallError("WireGuard endpoint port must be between 1 and 65535.")
    return host, port


def validate_wireguard(config: InstallConfig) -> str | None:
    if not config.wireguard_enabled:
        return None
    for value, description in (
        (config.wireguard_private_key, "Device private key"),
        (config.wireguard_public_key, "Device public key"),
        (config.wireguard_peer_public_key, "Server public key"),
    ):
        issue = validate_wireguard_key(value, description)
        if issue:
            return issue
    if config.wireguard_public_key == config.wireguard_peer_public_key:
        return "Server public key must be different from this machine's public key."
    try:
        split_wireguard_endpoint(config.wireguard_endpoint)
    except InstallError as exc:
        return str(exc)
    try:
        ipaddress.ip_interface(config.wireguard_address)
    except ValueError:
        return "Tunnel address must include a prefix, for example 10.20.0.2/24."
    return None


def derive_wireguard_public_key(private_key: str) -> str:
    """Derive a WireGuard public key from a validated private key via stdin."""

    issue = validate_wireguard_key(private_key, "Private key")
    if issue:
        raise InstallError(issue)
    try:
        public_result = subprocess.run(
            ["wg", "pubkey"],
            input=private_key + "\n",
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise InstallError(f"Cannot run wg to derive the public key: {exc}") from exc
    public_key = public_result.stdout.strip()
    issue = validate_wireguard_key(public_key, "Derived public key")
    if public_result.returncode or issue:
        detail = (public_result.stderr or issue or "wg pubkey returned an invalid key").strip()
        raise InstallError(f"WireGuard public-key derivation failed: {detail}")
    return public_key


def generate_wireguard_keypair() -> tuple[str, str]:
    """Generate a private/public pair without putting the private key in argv."""

    try:
        private_result = subprocess.run(
            ["wg", "genkey"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise InstallError(f"Cannot run wg to generate a key: {exc}") from exc
    private_key = private_result.stdout.strip()
    issue = validate_wireguard_key(private_key, "Generated private key")
    if private_result.returncode or issue:
        detail = (private_result.stderr or issue or "wg genkey returned an invalid key").strip()
        raise InstallError(f"WireGuard key generation failed: {detail}")
    return private_key, derive_wireguard_public_key(private_key)


def wireguard_configuration(config: InstallConfig, *, keepalive: int = WIREGUARD_KEEPALIVE) -> str:
    issue = validate_wireguard(config)
    if issue:
        raise InstallError(issue)
    return (
        "[Interface]\n"
        f"PrivateKey = {config.wireguard_private_key}\n"
        f"Address = {ipaddress.ip_interface(config.wireguard_address)}\n"
        "Table = off\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {config.wireguard_peer_public_key}\n"
        f"Endpoint = {config.wireguard_endpoint}\n"
        f"AllowedIPs = {WIREGUARD_ALLOWED_IPS}\n"
        f"PersistentKeepalive = {keepalive}\n"
    )


def wireguard_setconf(config: InstallConfig, *, keepalive: int) -> str:
    """Return a wg(8) config with wg-quick-only interface directives removed."""

    full = wireguard_configuration(config, keepalive=keepalive)
    return "\n".join(
        line
        for line in full.splitlines()
        if not line.startswith(("Address = ", "Table = "))
    ) + "\n"


def wireguard_qr_lines(value: str) -> list[str]:
    """Render a camera-readable terminal QR code containing only the public key."""

    try:
        import qrcode
    except ImportError as exc:
        raise InstallError("Python QR support is missing from the installer image.") from exc
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=1,
    )
    qr.add_data(value)
    qr.make(fit=True)
    return [
        "".join("██" if cell else "  " for cell in row)
        for row in qr.get_matrix()
    ]


def test_wireguard_tunnel(
    config: InstallConfig,
    *,
    interface: str = WIREGUARD_TEST_INTERFACE,
    timeout: int = WIREGUARD_TEST_TIMEOUT,
) -> str:
    """Bring up an isolated temporary peer and require a fresh handshake."""

    issue = validate_wireguard(config)
    if issue:
        raise InstallError(issue)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface):
        raise InstallError("Unsafe temporary WireGuard interface name.")
    host, port = split_wireguard_endpoint(config.wireguard_endpoint)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except OSError as exc:
        raise InstallError(f"Cannot resolve WireGuard endpoint {host}: {exc}") from exc

    existing = subprocess.run(
        ["ip", "link", "show", "dev", interface],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if existing.returncode == 0:
        raise InstallError(f"Temporary interface {interface} already exists; remove it first.")

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="wg-test-", suffix=".conf", dir=WORK_ROOT)
    config_path = Path(filename)

    def checked(command: Sequence[str], description: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise InstallError(f"{description} failed: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise InstallError(f"{description} failed: {detail[-600:]}")
        return result

    created = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(wireguard_setconf(config, keepalive=1))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(config_path, 0o600)
        checked(["ip", "link", "add", "dev", interface, "type", "wireguard"], "creating test tunnel")
        created = True
        checked(
            ["ip", "address", "add", config.wireguard_address, "dev", interface],
            "assigning test tunnel address",
        )
        checked(["wg", "setconf", interface, str(config_path)], "configuring test tunnel")
        checked(["ip", "link", "set", "up", "dev", interface], "starting test tunnel")

        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            result = checked(
                ["wg", "show", interface, "latest-handshakes"],
                "reading WireGuard handshake",
            )
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[0] == config.wireguard_peer_public_key:
                    try:
                        if int(fields[1]) > 0:
                            return "Handshake received from the configured WireGuard server."
                    except ValueError:
                        pass
            time.sleep(0.5)
        raise InstallError(
            "No WireGuard handshake was received. Add the displayed client public key "
            "to the server peer, then verify its endpoint, server public key, and firewall."
        )
    finally:
        config_path.unlink(missing_ok=True)
        if created:
            subprocess.run(
                ["ip", "link", "delete", "dev", interface],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )


def load_timezones() -> list[tuple[str, str]]:
    """Return zoneinfo entries without depending on a desktop locale service."""

    for table in (
        Path("/usr/share/zoneinfo/zone1970.tab"),
        Path("/usr/share/zoneinfo/zone.tab"),
    ):
        if not table.is_file():
            continue
        zones = [("UTC", "Coordinated Universal Time")]
        try:
            lines = table.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 3:
                continue
            zone = fields[2]
            description = fields[3] if len(fields) > 3 else fields[0]
            zones.append((zone, description or fields[0]))
        return sorted(set(zones), key=lambda item: item[0])
    return FALLBACK_TIMEZONES.copy()


def password_strength(value: str) -> tuple[str, str]:
    """Small, dependency-free password hint for the setup screen."""

    if len(value) < 8:
        return "TOO SHORT", "#fb7185"
    score = sum(
        (
            len(value) >= 12,
            bool(re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)),
            bool(re.search(r"\d", value)),
            bool(re.search(r"[^A-Za-z0-9]", value)),
        )
    )
    if score >= 4:
        return "STRONG", "#5eead4"
    if score >= 2:
        return "GOOD", "#fbbf24"
    return "BASIC", "#fbbf24"


def read_expected_sha256(path: Path) -> str:
    try:
        fields = path.read_text(encoding="ascii").split()
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallError(f"Cannot read bundle checksum {path}: {exc}") from exc
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise InstallError(f"Bundle checksum file is invalid: {path}")
    return fields[0].lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(COPY_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        raise InstallError(f"Cannot read installer bundle {path}: {exc}") from exc
    return digest.hexdigest()


def normalize_archive_path(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute():
        raise InstallError(f"Bundle contains an absolute path: {name!r}")
    parts = [part for part in path.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise InstallError(f"Bundle path escapes the target root: {name!r}")
    return "/" + "/".join(parts) if parts else "/"


def normalize_archive_link(path: str, linkname: str, *, hardlink: bool) -> str:
    if not linkname:
        raise InstallError(f"Bundle contains an empty link at {path}")
    if hardlink:
        if linkname.startswith("/"):
            raise InstallError(f"Bundle hardlink is absolute: {path} -> {linkname}")
        return normalize_archive_path(linkname)
    target = (
        posixpath.normpath(linkname)
        if linkname.startswith("/")
        else posixpath.normpath(posixpath.join(posixpath.dirname(path), linkname))
    )
    if not target.startswith("/") or target == "/.." or target.startswith("/../"):
        raise InstallError(f"Bundle symlink escapes the root: {path} -> {linkname}")
    return target


def copy_archive_member(
    member: tarfile.TarInfo,
    archive: tarfile.TarFile,
    destination: Path,
) -> str:
    if member.size <= 0 or member.size > MAX_BOOT_FILE_BYTES:
        raise InstallError(f"Invalid boot payload size for {member.name}: {member.size}")
    source = archive.extractfile(member)
    if source is None:
        raise InstallError(f"Cannot read boot payload {member.name}")
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as output:
            while chunk := source.read(COPY_CHUNK):
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise InstallError(f"Cannot stage boot payload {member.name}: {exc}") from exc
    return digest.hexdigest()


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


def inspect_bundle(payload: Path, staging: Path) -> BundleInfo:
    paths: set[str] = set()
    symlinks: set[str] = set()
    hardlinks: list[tuple[str, str]] = []
    boot_hashes: dict[str, str] = {}
    os_release: bytes | None = None
    entries = 0
    unpacked_bytes = 0
    decoder = subprocess.Popen(
        ["zstd", "-dc", "--", str(payload)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert decoder.stdout is not None and decoder.stderr is not None
    try:
        with tarfile.open(fileobj=decoder.stdout, mode="r|") as archive:
            for member in archive:
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES:
                    raise InstallError("Rootfs bundle contains too many entries")
                path = normalize_archive_path(member.name)
                current = path
                while current != "/":
                    paths.add(current)
                    current = posixpath.dirname(current)
                if member.issym():
                    normalize_archive_link(path, member.linkname, hardlink=False)
                    symlinks.add(path)
                elif member.islnk():
                    hardlinks.append(
                        (
                            path,
                            normalize_archive_link(path, member.linkname, hardlink=True),
                        )
                    )
                elif member.isfile():
                    unpacked_bytes += member.size
                    if unpacked_bytes > MAX_UNPACKED_BYTES:
                        raise InstallError("Uncompressed rootfs exceeds 128 GiB")
                    if path in BOOT_TRANSPORT:
                        if path in boot_hashes:
                            raise InstallError(f"Duplicate boot payload in bundle: {path}")
                        boot_hashes[path] = copy_archive_member(
                            member, archive, staging / BOOT_TRANSPORT[path]
                        )
                    elif path in {"/etc/os-release", "/usr/lib/os-release"}:
                        source = archive.extractfile(member)
                        if source is None:
                            raise InstallError(f"Cannot read {path} from bundle")
                        captured = source.read(MAX_METADATA_BYTES + 1)
                        if len(captured) > MAX_METADATA_BYTES:
                            raise InstallError(f"Bundle metadata is too large: {path}")
                        if path == "/etc/os-release" or os_release is None:
                            os_release = captured
    except InstallError:
        decoder.kill()
        decoder.wait()
        raise
    except (OSError, tarfile.TarError) as exc:
        decoder.kill()
        decoder.wait()
        raise InstallError(f"Cannot inspect rootfs bundle: {exc}") from exc
    finally:
        decoder.stdout.close()

    decoder_error = decoder.stderr.read().decode("utf-8", errors="replace").strip()
    if decoder.wait():
        raise InstallError(
            f"Cannot decompress rootfs bundle: {decoder_error or 'zstd failed'}"
        )
    for path in paths:
        parent = posixpath.dirname(path)
        while parent != "/":
            if parent in symlinks:
                raise InstallError(f"Bundle writes through symlink {parent}: {path}")
            parent = posixpath.dirname(parent)
    for path, target in hardlinks:
        if target not in paths:
            raise InstallError(f"Bundle hardlink target is missing: {path} -> {target}")

    required = (
        "/etc/passwd",
        "/etc/shadow",
        "/etc/group",
        "/etc/gshadow",
        "/usr/sbin/oko-update",
        "/usr/bin/wg",
        "/usr/bin/wg-quick",
    )
    missing = [path for path in required if path not in paths]
    missing.extend(path for path in BOOT_TRANSPORT if path not in boot_hashes)
    if not any(path in paths for path in ("/usr/bin/python3", "/bin/python3")):
        missing.append("python3")
    if not any(path in paths for path in ("/usr/bin/systemctl", "/bin/systemctl")):
        missing.append("systemctl")
    if missing:
        raise InstallError("Rootfs bundle is incomplete; missing " + ", ".join(missing))
    if os_release is None:
        raise InstallError("Rootfs bundle has no readable os-release")
    try:
        version = parse_os_release(os_release.decode("utf-8")).get("VERSION_ID", "")
    except UnicodeDecodeError as exc:
        raise InstallError("Rootfs bundle os-release is not UTF-8") from exc
    if not version:
        raise InstallError("Rootfs bundle os-release has no VERSION_ID")
    return BundleInfo(version, boot_hashes, entries, unpacked_bytes)


class InstallerEngine:
    def __init__(
        self,
        config: InstallConfig,
        reporter: Callable[[int, str], None] | None = None,
    ) -> None:
        if config.disk is None or config.interface is None:
            raise InstallError("Incomplete installation configuration")
        self.config = config
        self.reporter = reporter or (lambda _percent, _message: None)
        self.mounts: list[Path] = []
        self.workdir: Path | None = None
        self.install_media: Path | None = None
        self.payload: Path | None = None
        self.payload_sha256 = ""
        self.efi_loader: Path | None = None
        self.bundle_info: BundleInfo | None = None
        self.boot_partition = ""
        self.root_a_partition = ""
        self.root_b_partition = ""
        self.data_partition = ""
        self.preserved_data: PartitionInfo | None = None

    def report(self, percent: int, message: str) -> None:
        self.reporter(percent, message)

    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        chroot: bool = False,
        quiet: bool = False,
        allowed: Sequence[int] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        if chroot:
            argv = ["chroot", str(TARGET), *argv]
        if not quiet:
            self.report(-1, "$ " + " ".join(argv))
        try:
            result = subprocess.run(
                argv,
                input=input_text,
                stdin=subprocess.DEVNULL if input_text is None else None,
                text=True,
                check=False,
                capture_output=True,
            )
            if result.returncode not in allowed:
                detail = (result.stderr or result.stdout or "command failed").strip()
                raise InstallError(f"{argv[0]} failed: {detail[-600:]}")
            return result
        except FileNotFoundError as exc:
            raise InstallError(f"Required command is missing: {argv[0]}") from exc

    def write(self, relative: str, content: str, mode: int = 0o644) -> None:
        path = TARGET / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".oko-new")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, mode)
        temporary.replace(path)

    def mount(self, source: str, destination: Path, *options: str) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        self.run(["mount", *options, source, str(destination)])
        self.mounts.append(destination)

    def unmount_all(self) -> None:
        for path in reversed(self.mounts):
            subprocess.run(
                ["umount", "--recursive", str(path)],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
            )
        self.mounts.clear()

    def cleanup(self) -> None:
        self.unmount_all()
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    def install(self) -> None:
        try:
            self.preflight()
            self.release_disk()
            self.prepare_partitions()
            self.mount_root()
            self.mount_boot()
            self.mount_data()
            self.extract_rootfs()
            self.install_bootloader()
            self.configure_fstab()
            self.mount_chroot_filesystems()
            self.configure()
            self.report(100, "Installation complete")
        finally:
            self.cleanup()

    def preflight(self) -> None:
        self.report(2, "Checking installer media, update bundle, and required tools")
        if os.geteuid() != 0:
            raise InstallError("The installer must run as root")
        missing = [name for name in REQUIRED_COMMANDS if shutil.which(name) is None]
        if missing:
            raise InstallError("Missing installer commands: " + ", ".join(missing))

        self.install_media = find_install_media()
        self.payload = self.install_media / BUNDLE_RELATIVE
        checksum_path = self.install_media / BUNDLE_SHA256_RELATIVE
        if not self.payload.is_file() or self.payload.stat().st_size == 0:
            raise InstallError(f"Target rootfs bundle is missing: {self.payload}")
        if not checksum_path.is_file() or checksum_path.stat().st_size == 0:
            raise InstallError(f"Target bundle checksum is missing: {checksum_path}")
        self.efi_loader = next(
            (
                self.install_media / relative
                for relative in EFI_LOADER_RELATIVES
                if (self.install_media / relative).is_file()
            ),
            None,
        )
        if self.efi_loader is None:
            raise InstallError("The installer medium has no x86-64 UEFI GRUB loader")

        expected_hash = read_expected_sha256(checksum_path)
        actual_hash = sha256_file(self.payload)
        if actual_hash != expected_hash:
            raise InstallError(
                f"Rootfs bundle SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
            )
        self.payload_sha256 = actual_hash
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="work-", dir=WORK_ROOT))
        self.bundle_info = inspect_bundle(self.payload, self.workdir)
        if self.bundle_info.unpacked_bytes + MINIMUM_DATA_BYTES > ROOT_PARTITION_BYTES:
            raise InstallError("The rootfs bundle does not fit in a 24 GiB root slot")

        safe_disks = {item.path: item for item in discover_disks()}
        if self.config.disk.path not in safe_disks:
            raise InstallError("Selected disk disappeared or became unsafe")
        if safe_disks[self.config.disk.path].logical_sector != 512:
            raise InstallError("The current installer supports 512-byte logical sectors")
        if safe_disks[self.config.disk.path].size < MINIMUM_DISK_BYTES:
            raise InstallError("The selected disk is smaller than 50 GiB")
        if self.config.data_policy not in {"create", "keep", "clean"}:
            raise InstallError("Invalid oko-data installation policy")

        current_data = find_data_partition(self.config.disk.path)
        if self.config.data_policy == "keep":
            if current_data is None:
                raise InstallError(
                    "The oko-data partition selected for preservation disappeared"
                )
            if current_data.fstype != "ext4":
                raise InstallError(
                    f"Cannot preserve {current_data.path}: expected ext4, "
                    f"found {current_data.fstype or 'an unknown filesystem'}"
                )
            expected = self.config.existing_data
            if expected and (
                current_data.start_sector != expected.start_sector
                or current_data.end_sector != expected.end_sector
            ):
                raise InstallError(
                    "The oko-data partition geometry changed after it was selected"
                )
            table = self.run(
                ["lsblk", "-dn", "-o", "PTTYPE", self.config.disk.path],
                quiet=True,
            ).stdout.strip()
            if table != "gpt":
                raise InstallError(
                    "oko-data can be preserved only when it belongs to a GPT disk"
                )
            _fixed, data_start, last_usable = self.partition_plan()
            if current_data.start_sector < data_start:
                raise InstallError(
                    "The existing oko-data partition overlaps the two 24 GiB root slots"
                )
            if current_data.end_sector > last_usable:
                raise InstallError(
                    "The existing oko-data partition extends beyond the target disk"
                )
            if current_data.size < MINIMUM_DATA_BYTES:
                raise InstallError("The existing oko-data partition is too small")
            self.preserved_data = current_data
        elif self.config.data_policy == "create" and current_data is not None:
            raise InstallError(
                "An oko-data partition appeared after disk selection; review whether "
                "it should be kept or cleaned"
            )
        self.report(
            7,
            f"Verified OKO {self.bundle_info.version} bundle "
            f"({self.bundle_info.entries} archive entries)",
        )

    def disk_nodes(self) -> list[dict]:
        return inspect_disk_partitions(self.config.disk.path)

    def release_disk(self) -> None:
        disk = self.config.disk.path
        self.report(8, f"Releasing mounted filesystems on {disk}")
        # Keep bundle metadata and the staged kernel/initramfs until the final
        # cleanup. Only stale target mounts belong to this phase.
        self.unmount_all()
        nodes = self.disk_nodes()
        node_paths = {str(node.get("path") or "") for node in nodes}
        try:
            swap_lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
        except OSError as exc:
            raise InstallError(f"Cannot inspect active swap: {exc}") from exc
        for line in swap_lines:
            fields = line.split()
            if fields and fields[0] in node_paths:
                self.run(["swapoff", fields[0]])
        mountpoints: set[str] = set()
        for node in nodes:
            for mountpoint in node.get("mountpoints") or []:
                if mountpoint:
                    mountpoints.add(str(mountpoint))
        for mountpoint in sorted(
            mountpoints,
            key=lambda value: (value.count("/"), len(value)),
            reverse=True,
        ):
            self.run(["umount", "--recursive", mountpoint])

    def find_partition(self, label: str) -> PartitionInfo:
        for _attempt in range(30):
            for node in self.disk_nodes():
                if node.get("type") != "part":
                    continue
                if label not in {
                    str(node.get("label") or ""),
                    str(node.get("partlabel") or ""),
                }:
                    continue
                return partition_info(node)
            time.sleep(0.25)
        raise InstallError(f"Could not find the {label!r} partition on the target disk")

    def refresh_partitions(self) -> None:
        disk = self.config.disk.path
        self.run(["partprobe", disk])
        self.run(["udevadm", "settle"])

    def partition_plan(
        self,
    ) -> tuple[list[tuple[str, int, int, str]], int, int]:
        """Return fixed partition extents plus the first/last data sectors."""

        cursor = ALIGNMENT_SECTORS
        fixed: list[tuple[str, int, int, str]] = []
        for label, size, type_guid in (
            (BIOS_LABEL, BIOS_PARTITION_BYTES, GPT_TYPE_BIOS_BOOT),
            (BOOT_PARTLABEL, BOOT_PARTITION_BYTES, GPT_TYPE_EFI_SYSTEM),
            (ROOT_A_LABEL, ROOT_PARTITION_BYTES, GPT_TYPE_LINUX_FILESYSTEM),
            (ROOT_B_LABEL, ROOT_PARTITION_BYTES, GPT_TYPE_LINUX_FILESYSTEM),
        ):
            start = (
                (cursor + ALIGNMENT_SECTORS - 1) // ALIGNMENT_SECTORS
            ) * ALIGNMENT_SECTORS
            end = start + size // SECTOR_BYTES - 1
            fixed.append((label, start, end, type_guid))
            cursor = end + 1
        data_start = (
            (cursor + ALIGNMENT_SECTORS - 1) // ALIGNMENT_SECTORS
        ) * ALIGNMENT_SECTORS
        last_usable = self.config.disk.size // SECTOR_BYTES - 34
        minimum_data_sectors = MINIMUM_DATA_BYTES // SECTOR_BYTES
        if data_start + minimum_data_sectors - 1 > last_usable:
            raise InstallError(
                "The selected disk cannot hold two 24 GiB roots and oko-data"
            )
        return fixed, data_start, last_usable

    def create_partition(
        self,
        number: int,
        label: str,
        start: int,
        end: int,
        type_guid: str,
    ) -> None:
        size = end - start + 1
        if number <= 0 or start < 0 or size <= 0:
            raise InstallError(f"Invalid GPT geometry for partition {number}")

        # -N addresses the exact GPT entry, including an unused entry. Because
        # start and size are explicit sector counts, sfdisk does not realign
        # or resize the partition.
        specification = (
            f'start={start}, size={size}, type={type_guid}, name="{label}"\n'
        )
        self.run(
            [
                "sfdisk",
                *SFDISK_WRITE_OPTIONS,
                "-N",
                str(number),
                self.config.disk.path,
            ],
            input_text=specification,
        )

    def prepare_partitions(self) -> None:
        disk = self.config.disk.path
        fixed, data_start, last_usable = self.partition_plan()
        self.report(12, "Creating the BIOS/UEFI, A/B root, and data layout")
        if self.preserved_data:
            saved = self.preserved_data

            # Equivalent to sgdisk --move-second-header: move the backup GPT
            # header to the standard end-of-device location.
            self.run(
                [
                    "sfdisk",
                    *SFDISK_WRITE_OPTIONS,
                    "--relocate",
                    "gpt-bak-std",
                    disk,
                ]
            )

            for node in self.disk_nodes():
                if node.get("type") != "part":
                    continue
                number = int(node.get("partn") or 0)
                if number and number != saved.number:
                    self.run(
                        [
                            "sfdisk",
                            *SFDISK_WRITE_OPTIONS,
                            "--delete",
                            disk,
                            str(number),
                        ]
                    )

            # Preserve the data partition's exact start/end and PARTUUID; only
            # normalize its GPT type and partition name.
            self.run(
                [
                    "sfdisk",
                    *SFDISK_WRITE_OPTIONS,
                    "--part-type",
                    disk,
                    str(saved.number),
                    GPT_TYPE_LINUX_FILESYSTEM,
                ]
            )
            self.run(
                [
                    "sfdisk",
                    *SFDISK_WRITE_OPTIONS,
                    "--part-label",
                    disk,
                    str(saved.number),
                    DATA_LABEL,
                ]
            )
            used = {saved.number}
        else:
            # Replace any existing partition table with a fresh empty GPT.
            # --wipe always removes stale disk-label signatures, replacing
            # the old sgdisk --zap-all + --clear sequence.
            self.run(
                [
                    "sfdisk",
                    *SFDISK_WRITE_OPTIONS,
                    "--wipe",
                    "always",
                    disk,
                ],
                input_text="label: gpt\n",
            )
            used = set()

        def allocate(preferred: int) -> int:
            number = preferred
            while number in used:
                number += 1
            if number > 128:
                raise InstallError("The target GPT has no free partition number")
            used.add(number)
            return number

        for preferred, (label, start, end, type_guid) in enumerate(fixed, 1):
            self.create_partition(
                allocate(preferred), label, start, end, type_guid
            )
        if not self.preserved_data:
            self.create_partition(
                allocate(5),
                DATA_LABEL,
                data_start,
                last_usable,
                GPT_TYPE_LINUX_FILESYSTEM,
            )
        self.refresh_partitions()
        boot = self.find_partition(BOOT_PARTLABEL)
        root_a = self.find_partition(ROOT_A_LABEL)
        root_b = self.find_partition(ROOT_B_LABEL)
        data = self.find_partition(DATA_LABEL)

        if self.preserved_data and (
            data.start_sector != self.preserved_data.start_sector
            or data.end_sector != self.preserved_data.end_sector
        ):
            raise InstallError("The preserved oko-data GPT extent changed unexpectedly")

        self.report(24, "Formatting shared boot and both 24 GiB root slots")
        self.run(["mkfs.vfat", "-F", "32", "-n", BOOT_LABEL, boot.path])
        for root, label in ((root_a, ROOT_A_LABEL), (root_b, ROOT_B_LABEL)):
            self.run(
                ["mke2fs", "-t", "ext4", "-F", "-m", "0", "-L", label, root.path]
            )
        if not self.preserved_data:
            self.run(
                [
                    "mke2fs",
                    "-t",
                    "ext4",
                    "-F",
                    "-m",
                    "0",
                    "-L",
                    DATA_LABEL,
                    data.path,
                ]
            )
        else:
            self.report(-1, f"Preserved {data.path} without formatting or resizing it")
        self.run(["udevadm", "settle"])

        self.boot_partition = boot.path
        self.root_a_partition = root_a.path
        self.root_b_partition = root_b.path
        self.data_partition = data.path

    def mount_root(self) -> None:
        if not self.root_a_partition:
            raise InstallError("Root slot A was not identified")
        self.report(34, "Mounting root slot A")
        self.mount(self.root_a_partition, TARGET)

    def mount_boot(self) -> None:
        if not self.boot_partition:
            raise InstallError("The shared boot partition was not identified")
        self.mount(self.boot_partition, TARGET / "boot")

    def mount_data(self) -> None:
        if not self.data_partition:
            raise InstallError("The oko-data partition was not identified")
        action = "preserved" if self.preserved_data else "new"
        self.report(36, f"Mounting the {action} oko-data partition at /home")
        self.mount(self.data_partition, TARGET / DATA_MOUNTPOINT.lstrip("/"))

    def rootfs_exclusion_patterns(self) -> list[str]:
        patterns: list[str] = []
        for relative in (
            *ROOTFS_EXCLUDES,
            *(path.lstrip("/") for path in BOOT_TRANSPORT),
            *(path.lstrip("/") for path in LEGACY_BOOT_TRANSPORT_PATHS),
        ):
            patterns.extend(
                (relative, f"{relative}/*", f"./{relative}", f"./{relative}/*")
            )
        return patterns

    def extract_rootfs(self) -> None:
        if self.payload is None or self.bundle_info is None or self.workdir is None:
            raise InstallError("The update bundle was not prepared")
        if self.bundle_info.unpacked_bytes + MINIMUM_DATA_BYTES > shutil.disk_usage(TARGET).free:
            raise InstallError("Root slot A is too small for this release")
        exclude_file = self.workdir / "rootfs.exclude"
        exclude_file.write_text(
            "\n".join(self.rootfs_exclusion_patterns()) + "\n", encoding="utf-8"
        )
        log_file = self.workdir / "extract.log"
        self.report(42, f"Extracting OKO {self.bundle_info.version} into root slot A")
        with log_file.open("wb") as log:
            decoder = subprocess.Popen(
                ["zstd", "-dc", "--", str(self.payload)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
            )
            assert decoder.stdout is not None
            extractor = subprocess.Popen(
                [
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
                    "--anchored",
                    f"--exclude-from={exclude_file}",
                ],
                stdin=decoder.stdout,
                stdout=log,
                stderr=log,
            )
            decoder.stdout.close()
            tar_status = extractor.wait()
            zstd_status = decoder.wait()
        if tar_status or zstd_status:
            detail = log_file.read_text(encoding="utf-8", errors="replace")[-8192:].strip()
            raise InstallError(
                f"Rootfs extraction failed: {detail or (tar_status, zstd_status)}"
            )
        for directory in ("dev", "media", "mnt", "proc", "run", "sys", "tmp"):
            (TARGET / directory).mkdir(parents=True, exist_ok=True)

    def copy_boot_payload(self, source: Path, destination: Path, expected: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".oko-new")
        digest = hashlib.sha256()
        try:
            with source.open("rb") as input_file, temporary.open("xb") as output_file:
                while chunk := input_file.read(COPY_CHUNK):
                    output_file.write(chunk)
                    digest.update(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
            if digest.hexdigest() != expected:
                raise InstallError(f"Boot payload verification failed for {source.name}")
            os.chmod(temporary, 0o644)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def grub_configuration() -> str:
        return f"""# Generated by oko-installer. oko-update owns this file after setup.
set timeout=5
set default=0

insmod part_gpt
insmod fat

menuentry 'OKO Server (slot A)' --id oko-a {{
    search --no-floppy --label {BOOT_LABEL} --set=oko_boot
    set root=$oko_boot
    linux /oko/slots/a/bzImage root=PARTLABEL={ROOT_A_LABEL} oko.slot=a {KERNEL_ARGS}
}}
"""

    def install_bootloader(self) -> None:
        if (
            self.workdir is None
            or self.bundle_info is None
            or self.efi_loader is None
            or self.payload is None
        ):
            raise InstallError("Boot payloads were not prepared")
        self.report(66, "Installing the slot A kernel and bootloader")
        slot = TARGET / "boot/oko/slots/a"
        for archive_path, filename in BOOT_TRANSPORT.items():
            self.copy_boot_payload(
                self.workdir / filename,
                slot / filename,
                self.bundle_info.boot_hashes[archive_path],
            )

        payload_hash = self.payload_sha256
        release = {
            "version": self.bundle_info.version,
            "payload_sha256": payload_hash,
            "kernel_sha256": self.bundle_info.boot_hashes[
                "/usr/lib/oko-update/boot/bzImage"
            ],
        }
        self.write(
            "/boot/oko/slots/a/release.json",
            json.dumps(release, indent=2, sort_keys=True) + "\n",
        )
        self.write(
            "/boot/oko/state.json",
            json.dumps(
                {
                    "preferred_slot": "a",
                    "previous_slot": None,
                    "version": self.bundle_info.version,
                    "payload_sha256": payload_hash,
                    "status": "installed",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

        self.run(
            [
                "grub-install",
                "--target=i386-pc",
                f"--directory={find_grub_pc_directory()}",
                f"--boot-directory={TARGET / 'boot'}",
                "--recheck",
                self.config.disk.path,
            ]
        )
        efi_directory = TARGET / "boot/EFI/BOOT"
        efi_directory.mkdir(parents=True, exist_ok=True)
        efi_temporary = efi_directory / ".BOOTX64.EFI.oko-new"
        try:
            shutil.copyfile(self.efi_loader, efi_temporary)
            os.chmod(efi_temporary, 0o644)
            efi_temporary.replace(efi_directory / "BOOTX64.EFI")
        except OSError as exc:
            raise InstallError(f"Cannot install the UEFI loader: {exc}") from exc
        finally:
            efi_temporary.unlink(missing_ok=True)
        self.write(
            "/boot/EFI/BOOT/grub.cfg",
            "search --no-floppy --label OKO_BOOT --set=oko_boot\n"
            "set root=$oko_boot\n"
            "set prefix=($oko_boot)/grub\n"
            "configfile ($oko_boot)/grub/grub.cfg\n",
        )
        self.write("/boot/grub/grub.cfg", self.grub_configuration())
        os.sync()

    def configure_fstab(self) -> None:
        """Write the stable A/B, boot, and persistent-data mount contract."""

        path = TARGET / "etc/fstab"
        try:
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        except OSError as exc:
            raise InstallError(f"Cannot read the installed fstab: {exc}") from exc

        kept: list[str] = []
        for line in lines:
            fields = line.split()
            if not fields or line.lstrip().startswith("#"):
                kept.append(line)
                continue
            if len(fields) >= 2 and fields[1] in {"/", "/boot", DATA_MOUNTPOINT}:
                continue
            kept.append(line)
        if kept and kept[-1]:
            kept.append("")
        kept.extend(
            (
                f"PARTLABEL={ROOT_A_LABEL}\t/\text4\tdefaults\t0\t1",
                f"LABEL={BOOT_LABEL}\t/boot\tvfat\tdefaults\t0\t2",
                f"PARTLABEL={DATA_LABEL}\t{DATA_MOUNTPOINT}\text4\tdefaults\t0\t2",
            )
        )
        self.write("/etc/fstab", "\n".join(kept) + "\n")

    def mount_chroot_filesystems(self) -> None:
        """Provide kernel-managed filesystems required by chrooted tools."""
        self.report(76, "Preparing the installed system for configuration")

        target_dev = TARGET / "dev"
        self.mount("/dev", target_dev, "--rbind")
        self.run(["mount", "--make-rslave", str(target_dev)])

        self.mount(
            "proc",
            TARGET / "proc",
            "-t",
            "proc",
            "-o",
            "nosuid,noexec,nodev",
        )

        target_sys = TARGET / "sys"
        self.mount("/sys", target_sys, "--rbind")
        self.run(["mount", "--make-rslave", str(target_sys)])

    def configure_network(self) -> None:
        interface = self.config.interface
        assert interface is not None
        lines = ["[Match]"]
        if interface.mac and interface.mac != "00:00:00:00:00:00":
            lines.append(f"MACAddress={interface.mac}")
        else:
            lines.append(f"Name={interface.name}")
        lines.extend(["", "[Network]"])
        if self.config.network_mode == "dhcp":
            lines.extend(["DHCP=yes", "IPv6AcceptRA=yes"])
        else:
            lines.append(f"Address={self.config.address}")
            if self.config.gateway:
                lines.append(f"Gateway={self.config.gateway}")
            for server in parse_dns(self.config.dns):
                lines.append(f"DNS={server}")
        self.write("/etc/systemd/network/20-oko.network", "\n".join(lines) + "\n")

        resolv = TARGET / "etc/resolv.conf"
        if resolv.exists() or resolv.is_symlink():
            resolv.unlink()
        resolv.symlink_to("/run/systemd/resolve/stub-resolv.conf")

    def configure_wireguard(self) -> None:
        if not self.config.wireguard_enabled:
            return
        wireguard_directory = TARGET / "etc/wireguard"
        wireguard_directory.mkdir(parents=True, exist_ok=True)
        os.chmod(wireguard_directory, 0o700)
        self.write(
            f"/etc/wireguard/{WIREGUARD_INTERFACE}.conf",
            wireguard_configuration(self.config),
            mode=0o600,
        )
        self.run(
            [
                "/bin/systemctl",
                "enable",
                f"wg-quick@{WIREGUARD_INTERFACE}.service",
            ],
            chroot=True,
        )

    def configure(self) -> None:
        self.report(76, "Configuring server identity and administrator")
        self.write("/etc/hostname", self.config.hostname + "\n")
        self.write(
            "/etc/hosts",
            "127.0.0.1 localhost\n"
            f"127.0.1.1 {self.config.hostname}\n"
            "::1 localhost ip6-localhost ip6-loopback\n",
        )

        localtime = TARGET / "etc/localtime"
        if localtime.exists() or localtime.is_symlink():
            localtime.unlink()
        localtime.symlink_to(f"/usr/share/zoneinfo/{self.config.timezone}")
        self.write("/etc/timezone", self.config.timezone + "\n")

        self.run(
            [
                "/usr/sbin/useradd",
                "--create-home",
                "--uid",
                str(ADMIN_UID),
                "--user-group",
                "--groups",
                "sudo",
                "--shell",
                "/bin/bash",
                ADMIN_USER,
            ],
            chroot=True,
        )
        self.run(
            ["/usr/sbin/chpasswd"],
            input_text=f"{ADMIN_USER}:{self.config.password}\n",
            chroot=True,
            quiet=True,
        )
        self.run(["/usr/bin/passwd", "--lock", "root"], chroot=True)

        self.report(84, "Writing persistent systemd-networkd configuration")
        self.configure_network()
        if self.config.wireguard_enabled:
            self.report(87, "Writing the WireGuard tunnel configuration")
            self.configure_wireguard()

        self.write("/etc/machine-id", "")
        for host_key in (TARGET / "etc/ssh").glob("ssh_host_*_key*"):
            if host_key.is_file() or host_key.is_symlink():
                host_key.unlink()
        self.run(["/usr/bin/ssh-keygen", "-A"], chroot=True)
        self.run(["/bin/systemctl", "set-default", "multi-user.target"], chroot=True)
        self.report(96, "Flushing installed-system data")
        os.sync()


class OKOInstaller(App[None]):
    """Focused, keyboard-first installer for the OKO A/B system bundle."""

    TITLE = "OKO Server Installer"
    SUB_TITLE = f"v{INSTALLER_VERSION}"

    CSS = """
    Screen {
        width: 100%;
        height: 100%;
        align: center middle;
        background: #02060c;
        color: #eaf2fb;
    }

    #shell {
        width: 96%;
        max-width: 136;
        height: 94%;
        max-height: 48;
        min-height: 22;
        background: #07111d;
        border: round #35506b;
    }

    #topbar {
        width: 100%;
        height: 4;
        padding: 0 2;
        background: #091827;
        border-bottom: solid #28435d;
        content-align: left middle;
    }

    #brand {
        width: 9;
        height: 3;
        margin-right: 1;
        color: #020c11;
        background: #5eead4;
        content-align: center middle;
        text-align: center;
        text-style: bold;
    }

    #product-name {
        width: 1fr;
        height: 3;
        padding: 0 1;
        color: #dce7f3;
        content-align: left middle;
        text-style: bold;
    }

    #mode-badge {
        width: auto;
        min-width: 16;
        height: 3;
        padding: 0 2;
        color: #070714;
        background: #8b7cf6;
        content-align: center middle;
        text-align: center;
        text-style: bold;
    }

    #image-badge {
        width: auto;
        height: 3;
        padding: 0 1 0 2;
        color: #91a6ba;
        content-align: center middle;
    }

    #workspace {
        width: 100%;
        height: 1fr;
    }

    #rail {
        width: 29;
        height: 100%;
        padding: 1;
        background: #050d16;
        border-right: solid #28435d;
    }

    #rail-title {
        height: 2;
        padding: 0 1;
        color: #8599ad;
        text-style: bold;
    }

    .rail-step {
        width: 100%;
        height: 4;
        padding: 0 1;
        color: #657b91;
        border-left: thick #172a3d;
        content-align: left middle;
    }

    .rail-step.current {
        color: #ffffff;
        background: #10253a;
        border-left: thick #5eead4;
        text-style: bold;
    }

    .rail-step.done {
        color: #89d8ce;
        border-left: thick #2a7770;
    }

    #rail-note {
        height: 1fr;
        padding: 1;
        color: #71869a;
        content-align: left bottom;
    }

    #main {
        width: 1fr;
        height: 100%;
        padding: 1 3 0 3;
    }

    #page-kicker {
        height: 1;
        color: #5eead4;
        text-style: bold;
    }

    #page-title {
        height: 3;
        padding: 0 0 1 0;
        color: #ffffff;
        text-style: bold;
    }

    #page-description {
        width: 100%;
        height: auto;
        max-height: 3;
        margin-bottom: 1;
        padding: 0 1;
        color: #c0cedc;
        background: #0c2134;
        border-left: wide #5eead4;
    }

    #page-content {
        width: 100%;
        height: 1fr;
        align: center top;
    }

    #error-message {
        display: none;
        width: 100%;
        height: auto;
        max-height: 3;
        padding: 0 1;
        color: #fff0f2;
        background: #421420;
        border-left: wide #fb7185;
    }

    #actionbar {
        width: 100%;
        height: 3;
        padding: 0;
        border-top: solid #28435d;
        content-align: left middle;
    }

    #back-hint {
        width: auto;
        height: 2;
        padding: 0 1;
        color: #92a6ba;
        content-align: left middle;
    }

    #action-context {
        width: 1fr;
        height: 2;
        color: #657b91;
        text-align: center;
        content-align: center middle;
    }

    #enter-hint {
        width: auto;
        min-width: 21;
        height: 2;
        padding: 0 2;
        color: #031311;
        background: #5eead4;
        text-align: center;
        content-align: center middle;
        text-style: bold;
    }

    #enter-hint.danger {
        color: #1b050a;
        background: #fb7185;
    }

    #keybar {
        width: 100%;
        height: 2;
        padding: 0 2;
        color: #71869a;
        background: #030a12;
        border-top: solid #172a3d;
        text-align: center;
        content-align: center middle;
        text-style: bold;
    }

    .stage-panel {
        width: 100%;
        max-width: 96;
        height: auto;
        padding: 1 2;
        background: #091a2a;
        border: round #2b4964;
    }

    .stage-panel.full {
        height: 1fr;
    }

    .input-stage {
        max-width: 82;
    }

    .section-label {
        width: 100%;
        height: 2;
        padding: 0 1;
        color: #8fa5ba;
        content-align: left middle;
        text-style: bold;
    }

    .field-hint {
        width: 100%;
        height: auto;
        padding: 0 1;
        color: #8a9fb3;
    }

    .notice-card {
        width: 100%;
        height: auto;
        padding: 1 2;
        margin-bottom: 1;
        color: #d0dce8;
        background: #0d2337;
        border-left: wide #8b7cf6;
    }

    .danger-card {
        width: 100%;
        height: auto;
        padding: 1 2;
        margin-bottom: 1;
        color: #fff0f2;
        background: #37131d;
        border-left: wide #fb7185;
    }

    .metric-card {
        width: 100%;
        height: 3;
        padding: 0 1;
        margin-top: 1;
        color: #c5d2df;
        background: #0a1d2e;
        border-left: thick #8b7cf6;
        content-align: left middle;
    }

    Input {
        width: 100%;
        height: 3;
        margin: 1 0;
        padding: 0 1;
        color: #ffffff;
        background: #06111d;
        border: round #385873;
    }

    Input:focus {
        color: #ffffff;
        background: #102a40;
        border: round #5eead4;
        outline: solid #1a4954;
    }

    .choice-list {
        width: 100%;
        min-height: 5;
        padding: 0 1;
        color: #d6e1ec;
        background: #06121f;
        border: round #385873;
        scrollbar-color: #4e6981;
    }

    .choice-list > .option-list--option {
        padding: 0 2;
        color: #d6e1ec;
    }

    .choice-list > .option-list--option-highlighted {
        color: #ffffff;
        background: #173a50;
        text-style: bold;
    }

    .choice-list:focus {
        border: round #5eead4;
    }

    .choice-list:focus > .option-list--option-highlighted {
        color: #02110f;
        background: #5eead4;
        text-style: bold;
    }

    #disk-list {
        height: 8;
    }

    #network-interface {
        height: 12;
    }

    #disk-confirm-choice, #network-mode, #wireguard-choice,
    #wireguard-public-actions, #wireguard-test-actions, #review-choice,
    #power-actions, #result-actions {
        height: 7;
    }

    #wireguard-public-scroll {
        width: 100%;
        height: 1fr;
        align: center top;
        scrollbar-color: #4e6981;
    }

    #wireguard-public-key {
        width: 100%;
        height: 3;
        padding: 0 1;
        color: #5eead4;
        background: #06121f;
        border: round #385873;
        text-align: center;
        content-align: center middle;
        text-style: bold;
    }

    #wireguard-qr {
        width: auto;
        height: auto;
        margin: 1 0;
        padding: 1 2;
        color: #02060c;
        background: #eef5f7;
        text-align: center;
    }

    #timezone-regions, #timezone-cities {
        height: 1fr;
        min-height: 9;
    }

    #disk-details, #interface-details {
        width: 100%;
        height: 3;
        padding: 0 1;
        margin-top: 1;
        color: #becbd8;
        background: #0a1d2e;
        border-left: thick #8b7cf6;
        content-align: left middle;
    }

    #password-strength {
        width: 100%;
        height: 2;
        padding: 0 1;
        color: #91a6ba;
        content-align: left middle;
    }

    #summary-grid {
        width: 100%;
        height: auto;
        padding: 1;
        margin-bottom: 1;
        background: #081827;
        border: round #385873;
    }

    .summary-line {
        width: 100%;
        height: 2;
        padding: 0 1;
        color: #dce6f0;
        border-left: thick #8b7cf6;
    }

    #review-scroll {
        width: 100%;
        height: 1fr;
        padding-right: 1;
        scrollbar-color: #4e6981;
    }

    #progress-number {
        width: 100%;
        height: 3;
        color: #5eead4;
        text-align: right;
        content-align: right middle;
        text-style: bold;
    }

    #install-progress {
        width: 100%;
        height: 3;
        padding: 0 1;
    }

    #install-stage {
        width: 100%;
        height: auto;
        min-height: 2;
        max-height: 3;
        padding: 0 1;
        color: #d7e2ed;
        background: #0d2337;
        border-left: wide #5eead4;
    }

    #install-log {
        width: 100%;
        height: 1fr;
        min-height: 5;
        margin-top: 1;
        padding: 0 1;
        color: #afc0d1;
        background: #02070c;
        border: round #2b4964;
        scrollbar-color: #4e6981;
    }

    #result-badge {
        width: 100%;
        height: 5;
        padding: 1 2;
        margin-bottom: 1;
        content-align: left middle;
        text-style: bold;
    }

    #result-badge.success {
        color: #e5fffb;
        background: #0d3834;
        border-left: wide #5eead4;
    }

    #result-badge.failure {
        color: #fff0f2;
        background: #37131d;
        border-left: wide #fb7185;
    }

    #result-detail {
        width: 100%;
        height: auto;
        min-height: 5;
        padding: 1 2;
        margin-bottom: 1;
        color: #c3d0dd;
        background: #081827;
        border: round #385873;
    }

    ProgressBar > .bar--bar,
    ProgressBar > .bar--complete,
    ProgressBar > .bar--indeterminate {
        color: #5eead4;
    }

    #shell.compact #rail {
        display: none;
    }

    #shell.compact #image-badge {
        display: none;
    }

    #shell.compact #main,
    #shell.short #main {
        padding: 0 1;
    }

    #shell.compact #page-title,
    #shell.short #page-title {
        height: 2;
        padding-bottom: 0;
    }

    #shell.short #page-description {
        display: none;
    }

    #shell.compact .stage-panel,
    #shell.short .stage-panel {
        padding: 0 1;
        border: none;
    }

    #shell.compact .notice-card,
    #shell.compact .danger-card,
    #shell.short .notice-card,
    #shell.short .danger-card {
        padding: 0 1;
        margin-bottom: 0;
    }

    #shell.compact Input,
    #shell.short Input {
        height: 1;
        margin: 0;
        border: none;
    }

    #shell.compact .section-label,
    #shell.short .section-label,
    #shell.compact .summary-line,
    #shell.short .summary-line {
        height: 1;
    }

    #shell.compact #disk-list,
    #shell.compact #network-interface,
    #shell.short #disk-list,
    #shell.short #network-interface,
    #shell.compact #timezone-regions,
    #shell.compact #timezone-cities,
    #shell.short #timezone-regions,
    #shell.short #timezone-cities {
        height: 1fr;
        min-height: 5;
    }

    #shell.compact #disk-details,
    #shell.compact #interface-details,
    #shell.short #disk-details,
    #shell.short #interface-details {
        height: 2;
        margin-top: 0;
    }

    #shell.compact #actionbar,
    #shell.short #actionbar {
        height: 2;
    }

    #shell.compact #keybar,
    #shell.short #keybar {
        height: 1;
        border-top: none;
    }

    #shell.short #install-progress {
        height: 2;
        padding: 0;
    }

    #shell.short #progress-number {
        height: 1;
    }

    #shell.short #install-log {
        min-height: 3;
        margin-top: 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f10", "quit_installer", "Close installer", priority=True, show=False),
        Binding("ctrl+c", "power_options", "Power options", priority=True, show=False),
        Binding("ctrl+p", "power_options", "Power options", priority=True, show=False),
        Binding("escape", "go_back", "Back", priority=True, show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = InstallConfig()
        self.current_step = "disk"
        self.page_index = 0
        self.mode = "wizard"
        self.disks: list[Disk] = []
        self.interfaces: list[Interface] = []
        self.disk_option_map: dict[str, Disk] = {}
        self.interface_option_map: dict[str, Interface] = {}
        self.timezones = load_timezones()
        self.timezone_region: str | None = None
        self.timezone_option_map: dict[str, str] = {}
        self.password_confirmation = ""
        self.wireguard_test_message = "Not tested yet"
        self.wireguard_testing = False
        self.disk_scan_error: str | None = None
        self.interface_scan_error: str | None = None
        self.logs: list[str] = []
        self.current_percent = 0
        self.install_success = False
        self.result_message = ""
        self._navigation_locked = False
        self._power_return_mode = "wizard"
        self._power_return_step = "disk"

    def compose(self) -> ComposeResult:
        with Container(id="shell"):
            with Horizontal(id="topbar"):
                yield Static("OKO", id="brand")
                yield Static("SERVER  /  INSTALLER", id="product-name")
                yield Static("GUIDED SETUP", id="mode-badge")
                yield Static("BIOS + UEFI A/B", id="image-badge")
            with Horizontal(id="workspace"):
                with Vertical(id="rail"):
                    yield Static("INSTALL PLAN", id="rail-title")
                    for index, name in enumerate(WIZARD_STEPS):
                        yield Static(
                            f"{index + 1:02d}  {name.upper()}\n    Not configured",
                            id=f"rail-step-{index}",
                            classes="rail-step",
                        )
                    yield Static(
                        "SAFE SETUP\nDisk unchanged until final confirmation",
                        id="rail-note",
                    )
                with Vertical(id="main"):
                    yield Static("01 STORAGE  /  01 OF 02", id="page-kicker")
                    yield Static(PAGE_TITLES[0], id="page-title")
                    yield Static(PAGE_DESCRIPTIONS[0], id="page-description")
                    yield Vertical(id="page-content")
                    yield Static("", id="error-message")
                    with Horizontal(id="actionbar"):
                        yield Static("CTRL+P  POWER", id="back-hint")
                        yield Static("NO CHANGES YET", id="action-context")
                        yield Static("ENTER  SELECT  >", id="enter-hint")
            yield Static(
                "UP/DOWN  CHOOSE     ENTER  SELECT     ESC  BACK     F10  CLOSE",
                id="keybar",
            )

    async def on_mount(self) -> None:
        await self._scan_hardware()
        await self.show_step("disk")

    def on_resize(self, event: Resize) -> None:
        shell = self.query_one("#shell", Container)
        shell.set_class(event.size.width < 96, "compact")
        shell.set_class(event.size.height < 38, "short")

    async def _scan_hardware(self) -> None:
        await self._scan_disks()
        await self._scan_interfaces()

    async def _scan_disks(self) -> None:
        self.disk_scan_error = None
        try:
            self.disks = await asyncio.to_thread(discover_disks)
        except InstallError as exc:
            self.disks = []
            self.disk_scan_error = str(exc)

        safe_paths = {disk.path for disk in self.disks}
        if self.config.disk and self.config.disk.path not in safe_paths:
            self.config.disk = None
            self.config.existing_data = None
            self.config.data_policy = "create"

    async def _inspect_selected_data(self) -> bool:
        assert self.config.disk
        try:
            existing = await asyncio.to_thread(
                find_data_partition, self.config.disk.path
            )
        except InstallError as exc:
            self.config.existing_data = None
            self.config.data_policy = "create"
            self._set_error(str(exc))
            return False
        self.config.existing_data = existing
        if existing is None:
            self.config.data_policy = "create"
        elif existing.fstype == "ext4":
            self.config.data_policy = "keep"
        else:
            self.config.data_policy = "clean"
        return True

    async def _scan_interfaces(self) -> None:
        self.interface_scan_error = None
        try:
            self.interfaces = await asyncio.to_thread(discover_interfaces)
        except OSError as exc:
            self.interfaces = []
            self.interface_scan_error = (
                f"Cannot enumerate network interfaces: {exc}"
            )

        interface_names = {interface.name for interface in self.interfaces}
        if self.config.interface and self.config.interface.name not in interface_names:
            self.config.interface = None

    async def _replace_content(self, *widgets: object) -> None:
        content = self.query_one("#page-content", Vertical)
        await content.remove_children()
        if widgets:
            await content.mount(*widgets)

    def _set_error(self, message: str) -> None:
        error = self.query_one("#error-message", Static)
        error.update(escape(message))
        error.styles.display = "block"

    def _clear_error(self) -> None:
        error = self.query_one("#error-message", Static)
        error.update("")
        error.styles.display = "none"

    def _set_header_mode(self, label: str, color: str) -> None:
        badge = self.query_one("#mode-badge", Static)
        badge.update(label)
        badge.styles.background = color

    def _update_rail(self) -> None:
        completed = self.mode in {"review", "progress", "result"}
        if self.config.disk:
            if self.config.data_policy == "keep":
                storage_summary = f"{self.config.disk.path} / keep data"
            elif self.config.data_policy == "clean":
                storage_summary = f"{self.config.disk.path} / clean data"
            else:
                storage_summary = f"{self.config.disk.path} / new data"
        else:
            storage_summary = "Choose target drive"
        summaries = (
            storage_summary,
            (
                f"{self.config.hostname} / {ADMIN_USER}"
                if self.page_index > 0 or completed
                else "Server identity"
            ),
            (
                f"{self.config.interface.name} / {self.config.network_mode.upper()}"
                + (" / WG" if self.config.wireguard_enabled else "")
                if self.config.interface
                else "Persistent network"
            ),
        )
        for index, name in enumerate(WIZARD_STEPS):
            item = self.query_one(f"#rail-step-{index}", Static)
            item.remove_class("current", "done")
            if completed or index < self.page_index:
                item.add_class("done")
                marker = "OK"
            elif index == self.page_index:
                item.add_class("current")
                marker = ">>"
            else:
                marker = f"{index + 1:02d}"
            item.update(
                f"{marker:>2}  {name.upper()}\n    {escape(summaries[index])}"
            )

    def _set_action_hints(
        self, *, left: str, context: str, enter: str, danger: bool = False
    ) -> None:
        bar = self.query_one("#actionbar", Horizontal)
        bar.styles.display = "block"
        self.query_one("#back-hint", Static).update(left)
        self.query_one("#action-context", Static).update(context)
        enter_hint = self.query_one("#enter-hint", Static)
        enter_hint.update(enter)
        enter_hint.set_class(danger, "danger")

    @staticmethod
    def _stage(*widgets: object, full: bool = False, input_stage: bool = False) -> Vertical:
        classes = ["stage-panel"]
        if full:
            classes.append("full")
        if input_stage:
            classes.append("input-stage")
        return Vertical(*widgets, classes=" ".join(classes))

    def _disk_widgets(self) -> list[object]:
        self.disk_option_map = {}
        options: list[Option] = []
        selected_index = 0
        for index, disk in enumerate(self.disks):
            option_id = f"disk-{index}"
            self.disk_option_map[option_id] = disk
            options.append(
                Option(
                    f"[bold]{escape(disk.path)}[/bold]  {escape(disk.size_text)}"
                    f"\n{escape(disk.model)}  /  {escape(disk.transport)}  /  "
                    f"{disk.logical_sector}-byte sectors",
                    id=option_id,
                )
            )
            if self.config.disk and disk.path == self.config.disk.path:
                selected_index = index

        if not options:
            options.append(
                Option(
                    "[bold]NO WRITABLE DRIVE FOUND[/bold]\n"
                    "Attach at least 50 GiB, then scan again",
                    id="disk-none",
                    disabled=True,
                )
            )
        options.append(
            Option(
                "[bold]SCAN FOR DRIVES AGAIN[/bold]\nRefresh the safe target list",
                id="disk-rescan",
            )
        )
        disk_list = OptionList(*options, id="disk-list", classes="choice-list")
        disk_list.highlighted = selected_index if self.disks else len(options) - 1
        first = self.disks[selected_index] if self.disks else None
        details = (
            f"{first.path}  /  {first.size_text}  /  {first.model}. "
            "Selection does not write anything yet."
            if first
            else "The live medium and mounted system drives are excluded automatically."
        )
        return [
            self._stage(
                Static("AVAILABLE INSTALLATION DRIVES", classes="section-label"),
                disk_list,
                Static(escape(details), id="disk-details"),
            )
        ]

    def _disk_confirmation_widgets(self) -> list[object]:
        assert self.config.disk
        disk = self.config.disk
        keeping = self.config.data_policy == "keep" and self.config.existing_data
        if keeping:
            warning = (
                f"[bold]SYSTEM PARTITIONS ON {escape(disk.path)} WILL BE REPLACED[/bold]\n"
                f"{escape(keeping.path)}  /  {escape(keeping.size_text)} will keep its "
                "existing files and exact disk extent."
            )
            action = (
                f"[bold]USE {escape(disk.path)} AND KEEP OKO-DATA[/bold]\n"
                "Continue; system writing still waits for final review"
            )
        else:
            data_note = (
                "The existing oko-data filesystem will be erased and recreated."
                if self.config.data_policy == "clean"
                else "A new oko-data filesystem will use the remaining disk space."
            )
            warning = (
                f"[bold]THE INSTALLED IMAGE WILL REPLACE {escape(disk.path)}[/bold]\n"
                f"{escape(disk.size_text)}  /  {escape(disk.model)}  /  "
                "two fresh 24 GiB root slots will be created. "
                f"{data_note}"
            )
            action = (
                f"[bold]USE {escape(disk.path)}[/bold]\n"
                "Continue; erasure still waits for final review"
            )
        return [
            self._stage(
                Static(warning, classes="danger-card"),
                OptionList(
                    Option(
                        "[bold]CHOOSE A DIFFERENT DRIVE[/bold]\n"
                        "SAFE - return without changing anything",
                        id="disk-confirm-back",
                    ),
                    Option(
                        action,
                        id="disk-confirm-use",
                    ),
                    id="disk-confirm-choice",
                    classes="choice-list",
                ),
            )
        ]

    def _data_policy_widgets(self) -> list[object]:
        assert self.config.existing_data
        data = self.config.existing_data
        can_keep = data.fstype == "ext4"
        choices = OptionList(
            Option(
                (
                    "[bold]KEEP EXISTING OKO-DATA[/bold]\n"
                    "Recommended - preserve every file; do not format or resize it"
                    if can_keep
                    else "[bold]KEEP IS UNAVAILABLE[/bold]\n"
                    "Only an ext4 oko-data filesystem can be preserved safely"
                ),
                id="data-keep",
                disabled=not can_keep,
            ),
            Option(
                "[bold]CLEAN AND RECREATE OKO-DATA[/bold]\n"
                "Erase its files and use all space after the A/B system partitions",
                id="data-clean",
            ),
            id="data-policy-choice",
            classes="choice-list",
        )
        choices.highlighted = 0 if can_keep and self.config.data_policy == "keep" else 1
        return [
            self._stage(
                Static(
                    f"[bold]{escape(data.path)}[/bold]  /  {escape(data.size_text)}  /  "
                    f"{escape(data.fstype or 'unknown filesystem')}\n"
                    "OKO found persistent data on this drive. Choose what the "
                    "reinstall should do.",
                    classes="metric-card",
                ),
                choices,
            )
        ]

    def _input_widgets(
        self,
        *,
        label: str,
        value: str,
        placeholder: str,
        input_id: str,
        hint: str,
        max_length: int,
        password: bool = False,
        extra: object | None = None,
    ) -> list[object]:
        widgets: list[object] = [
            Static(label, classes="section-label"),
            Input(
                value=value,
                placeholder=placeholder,
                id=input_id,
                max_length=max_length,
                password=password,
            ),
        ]
        if extra is not None:
            widgets.append(extra)
        widgets.append(Static(hint, classes="field-hint"))
        return [self._stage(*widgets, input_stage=True)]

    def _timezone_widgets(self, *, cities: bool) -> list[object]:
        self.timezone_option_map = {}
        if not cities:
            regions = sorted(
                {
                    zone.split("/", 1)[0]
                    for zone, _description in self.timezones
                    if "/" in zone
                }
            )
            options = [
                Option(
                    "[bold]UTC[/bold]\nCoordinated Universal Time",
                    id="zone-utc",
                )
            ]
            self.timezone_option_map["zone-utc"] = "UTC"
            selected = 0
            preferred_region = self.timezone_region
            if preferred_region is None and "/" in self.config.timezone:
                preferred_region = self.config.timezone.split("/", 1)[0]
            for position, region in enumerate(regions):
                option_id = f"region-{position}"
                self.timezone_option_map[option_id] = region
                count = sum(
                    1
                    for zone, _description in self.timezones
                    if zone.startswith(region + "/")
                )
                options.append(
                    Option(
                        f"[bold]{escape(region)}[/bold]\n{count} available timezones",
                        id=option_id,
                    )
                )
                if region == preferred_region:
                    selected = len(options) - 1
            choices = OptionList(
                *options, id="timezone-regions", classes="choice-list"
            )
            choices.highlighted = selected
            return [
                self._stage(
                    Static("REGION", classes="section-label"),
                    choices,
                    full=True,
                )
            ]

        assert self.timezone_region
        zones = [
            (zone, description)
            for zone, description in self.timezones
            if zone.startswith(self.timezone_region + "/")
        ]
        options: list[Option] = []
        selected = 0
        for position, (zone, description) in enumerate(zones):
            option_id = f"timezone-{position}"
            self.timezone_option_map[option_id] = zone
            city = zone.split("/", 1)[1].replace("_", " ")
            options.append(
                Option(
                    f"[bold]{escape(city)}[/bold]\n{escape(description)}",
                    id=option_id,
                )
            )
            if zone == self.config.timezone:
                selected = position
        choices = OptionList(
            *options, id="timezone-cities", classes="choice-list"
        )
        choices.highlighted = selected
        return [
            self._stage(
                Static(
                    f"REGION  /  [bold]{escape(self.timezone_region)}[/bold]",
                    classes="section-label",
                ),
                choices,
                full=True,
            )
        ]

    def _interface_widgets(self) -> list[object]:
        self.interface_option_map = {}
        interface_options: list[Option] = []
        selected_index = 0
        for index, interface in enumerate(self.interfaces):
            option_id = f"interface-{index}"
            self.interface_option_map[option_id] = interface
            interface_options.append(
                Option(
                    f"[bold]{escape(interface.name)}[/bold]  "
                    f"{escape(interface.state.upper())}\n"
                    f"Hardware address  {escape(interface.mac)}",
                    id=option_id,
                )
            )
            if self.config.interface and interface.name == self.config.interface.name:
                selected_index = index
        if not interface_options:
            interface_options.append(
                Option(
                    "[bold]NO NETWORK INTERFACE FOUND[/bold]\n"
                    "Connect hardware or load its driver",
                    id="interface-none",
                    disabled=True,
                )
            )
        interface_options.append(
            Option(
                "[bold]SCAN FOR INTERFACES AGAIN[/bold]\nRefresh the hardware list",
                id="interface-rescan",
            )
        )

        interface_list = OptionList(
            *interface_options,
            id="network-interface",
            classes="choice-list",
        )
        interface_list.highlighted = (
            selected_index if self.interfaces else len(interface_options) - 1
        )
        first = self.interfaces[selected_index] if self.interfaces else None
        details = (
            f"{first.name}  /  {first.mac}  /  link {first.state}. "
            "The installed profile follows this hardware address."
            if first
            else "Loopback is hidden. Physical and virtual network links are supported."
        )
        return [
            self._stage(
                Static("NETWORK HARDWARE", classes="section-label"),
                interface_list,
                Static(escape(details), id="interface-details"),
            )
        ]

    def _network_mode_widgets(self) -> list[object]:
        choices = OptionList(
            Option(
                "[bold]AUTOMATIC  /  DHCP[/bold]\n"
                "Recommended - address, gateway, and DNS are automatic",
                id="mode-dhcp",
            ),
            Option(
                "[bold]STATIC ADDRESS[/bold]\nKeep a fixed server address",
                id="mode-static",
            ),
            id="network-mode",
            classes="choice-list",
        )
        choices.highlighted = 1 if self.config.network_mode == "static" else 0
        return [
            self._stage(
                Static("ADDRESS METHOD", classes="section-label"),
                choices,
                Static(
                    "This profile is saved in the target system and activates on first boot.",
                    classes="metric-card",
                ),
            )
        ]

    def _wireguard_choice_widgets(self) -> list[object]:
        choices = OptionList(
            Option(
                "[bold]NO WIREGUARD TUNNEL[/bold]\n"
                "Continue with the physical network only",
                id="wireguard-disabled",
            ),
            Option(
                "[bold]GENERATE A NEW PRIVATE KEY[/bold]\n"
                "Create a device key locally, register its public key, and test a handshake",
                id="wireguard-generate",
            ),
            Option(
                "[bold]USE AN EXISTING PRIVATE KEY[/bold]\n"
                "Paste an existing device private key and derive its public key locally",
                id="wireguard-existing",
            ),
            id="wireguard-choice",
            classes="choice-list",
        )
        if not self.config.wireguard_enabled:
            choices.highlighted = 0
        elif self.config.wireguard_key_source == "existing":
            choices.highlighted = 2
        else:
            choices.highlighted = 1
        return [
            self._stage(
                Static("OPTIONAL SECURE TUNNEL", classes="section-label"),
                choices,
                Static(
                    "Generated and pasted private keys remain on this machine. "
                    "Only the derived public key is displayed.",
                    classes="metric-card",
                ),
            )
        ]

    def _wireguard_public_widgets(self) -> list[object]:
        public_key = self.config.wireguard_public_key
        try:
            qr_text = "\n".join(wireguard_qr_lines(public_key))
        except InstallError as exc:
            qr_text = f"QR unavailable: {exc}"
        secondary_action = (
            Option(
                "[bold]ENTER A DIFFERENT PRIVATE KEY[/bold]\n"
                "Return to the private-key field and derive another public key",
                id="wireguard-public-change-private",
            )
            if self.config.wireguard_key_source == "existing"
            else Option(
                "[bold]GENERATE A NEW DEVICE KEY[/bold]\n"
                "Invalidate this public key and show a replacement",
                id="wireguard-public-regenerate",
            )
        )
        choices = OptionList(
            Option(
                "[bold]KEY ADDED TO THE WIREGUARD SERVER[/bold]\n"
                "Continue with the server peer settings",
                id="wireguard-public-continue",
            ),
            secondary_action,
            id="wireguard-public-actions",
            classes="choice-list",
        )
        choices.highlighted = 0
        return [
            VerticalScroll(
                Static(
                    "Add this machine as a peer on your WireGuard server. "
                    "Scan the QR code or copy the exact public key printed directly below it. "
                    "The private key is never displayed here.",
                    classes="notice-card",
                ),
                Static(qr_text, id="wireguard-qr", markup=False),
                Static("PUBLIC KEY  /  TEXT", classes="section-label"),
                Static(escape(public_key), id="wireguard-public-key"),
                choices,
                id="wireguard-public-scroll",
                classes="stage-panel full",
            )
        ]

    def _wireguard_test_widgets(self) -> list[object]:
        status = (
            "[bold #5eead4]HANDSHAKE VERIFIED[/]  /  "
            + escape(self.wireguard_test_message)
            if self.config.wireguard_tested
            else "[bold #fbbf24]NOT VERIFIED[/]  /  "
            + escape(self.wireguard_test_message)
        )
        choices = OptionList(
            Option(
                "[bold]TEST WIREGUARD HANDSHAKE[/bold]\n"
                "Use the live network and a temporary tunnel; only its connected subnet is routed",
                id="wireguard-test-run",
            ),
            Option(
                "[bold]CONTINUE TO FINAL REVIEW[/bold]\n"
                "Save the configuration even if its handshake was not verified",
                id="wireguard-test-continue",
            ),
            id="wireguard-test-actions",
            classes="choice-list",
        )
        choices.highlighted = 1 if self.config.wireguard_tested else 0
        return [
            self._stage(
                Static(status, classes="notice-card"),
                choices,
                Static(
                    "Before testing, the server must contain the public key shown on the previous screen. "
                    "AllowedIPs is fixed to 0.0.0.0/0, but no default route is installed: only the subnet "
                    "from this machine's tunnel address is connected to wg0.",
                    classes="field-hint",
                ),
            )
        ]

    def _review_widgets(self) -> list[object]:
        assert self.config.disk and self.config.interface
        network = (
            f"{self.config.interface.name} / DHCP"
            if self.config.network_mode == "dhcp"
            else f"{self.config.interface.name} / {self.config.address}"
        )
        wireguard = (
            f"wg0 / {self.config.wireguard_address} / "
            f"{'verified' if self.config.wireguard_tested else 'not tested'}"
            if self.config.wireguard_enabled
            else "disabled"
        )
        if self.config.data_policy == "keep" and self.config.existing_data:
            data_summary = (
                f"KEEP {self.config.existing_data.path} / "
                f"{self.config.existing_data.size_text}"
            )
            install_title = "REINSTALL OKO AND KEEP OKO-DATA"
            warning = (
                "[bold]SYSTEM PARTITIONS ON "
                f"{escape(self.config.disk.path)} WILL BE DESTROYED.[/bold]\n"
                f"{escape(self.config.existing_data.path)} is preserved without formatting "
                "or filesystem resizing. The administrator password is never displayed here."
            )
        else:
            data_summary = (
                "CLEAN AND RECREATE / remaining disk space"
                if self.config.data_policy == "clean"
                else "CREATE / remaining disk space"
            )
            install_title = f"ERASE {self.config.disk.path} AND INSTALL OKO"
            warning = (
                f"[bold]ALL DATA ON {escape(self.config.disk.path)} WILL BE DESTROYED.[/bold]\n"
                "The administrator password is configured but never displayed here."
            )
        summary = Vertical(
            Static(
                f"[bold]TARGET[/bold]  {escape(self.config.disk.path)}  /  "
                f"{escape(self.config.disk.size_text)}  /  {escape(self.config.disk.model)}",
                classes="summary-line",
            ),
            Static(
                f"[bold]SERVER[/bold]  {escape(self.config.hostname)}    "
                f"[bold]ADMIN[/bold]  {escape(ADMIN_USER)}",
                classes="summary-line",
            ),
            Static(
                f"[bold]TIME[/bold]  {escape(self.config.timezone)}    "
                f"[bold]NETWORK[/bold]  {escape(network)}",
                classes="summary-line",
            ),
            Static(
                f"[bold]LAYOUT[/bold]  A/B roots / 24 GiB each    "
                f"[bold]DATA[/bold]  {escape(data_summary)}",
                classes="summary-line",
            ),
            Static(
                f"[bold]WIREGUARD[/bold]  {escape(wireguard)}",
                classes="summary-line",
            ),
            id="summary-grid",
        )
        choices = OptionList(
            Option(
                "[bold]GO BACK AND REVIEW[/bold]\n"
                "SAFE - the target drive remains unchanged",
                id="review-back",
            ),
            Option(
                f"[bold]{escape(install_title)}[/bold]\n"
                "FINAL - writing starts immediately",
                id="review-install",
            ),
            id="review-choice",
            classes="choice-list",
        )
        choices.highlighted = 0
        return [
            VerticalScroll(
                summary,
                Static(warning, classes="danger-card"),
                choices,
                id="review-scroll",
                classes="stage-panel full",
            )
        ]

    def _widgets_for_step(self, step: str) -> list[object]:
        if step == "disk":
            return self._disk_widgets()
        if step == "data-policy":
            return self._data_policy_widgets()
        if step == "disk-confirm":
            return self._disk_confirmation_widgets()
        if step == "hostname":
            return self._input_widgets(
                label="SERVER NAME",
                value=self.config.hostname,
                placeholder="oko-server",
                input_id="hostname-input",
                hint="Use one DNS label: letters, numbers, and hyphens. Example: edge-01.",
                max_length=63,
            )
        if step == "password":
            strength, color = password_strength(self.config.password)
            return self._input_widgets(
                label="ADMINISTRATOR PASSWORD",
                value=self.config.password,
                placeholder="At least 8 characters",
                input_id="password-input",
                hint="Use a unique password. It is never written to the installer log.",
                max_length=128,
                password=True,
                extra=Static(
                    f"PASSWORD QUALITY  /  [{color}]{strength}[/{color}]",
                    id="password-strength",
                ),
            )
        if step == "password-confirm":
            return self._input_widgets(
                label="CONFIRM ADMINISTRATOR PASSWORD",
                value=self.password_confirmation,
                placeholder="Enter the same password again",
                input_id="confirm-input",
                hint="Press Enter after retyping it. The password itself remains hidden.",
                max_length=128,
                password=True,
            )
        if step == "timezone-region":
            return self._timezone_widgets(cities=False)
        if step == "timezone-city":
            return self._timezone_widgets(cities=True)
        if step == "interface":
            return self._interface_widgets()
        if step == "network-mode":
            return self._network_mode_widgets()
        if step == "address":
            return self._input_widgets(
                label="STATIC ADDRESS  /  CIDR PREFIX",
                value=self.config.address,
                placeholder="192.168.1.50/24",
                input_id="address-input",
                hint="Include the prefix length. IPv4 and IPv6 are accepted.",
                max_length=64,
            )
        if step == "gateway":
            return self._input_widgets(
                label="DEFAULT GATEWAY  /  OPTIONAL",
                value=self.config.gateway,
                placeholder="192.168.1.1",
                input_id="gateway-input",
                hint="Leave empty when this network has no default route.",
                max_length=64,
            )
        if step == "dns":
            return self._input_widgets(
                label="DNS SERVERS",
                value=self.config.dns,
                placeholder="1.1.1.1 9.9.9.9",
                input_id="dns-input",
                hint="Separate multiple server addresses with spaces or commas.",
                max_length=128,
            )
        if step == "wireguard-choice":
            return self._wireguard_choice_widgets()
        if step == "wireguard-private-key":
            return self._input_widgets(
                label="EXISTING WIREGUARD PRIVATE KEY",
                value=(
                    self.config.wireguard_private_key
                    if self.config.wireguard_key_source == "existing"
                    else ""
                ),
                placeholder="Base64 private key",
                input_id="wireguard-private-key-input",
                hint=(
                    "Paste the 44-character base64 private key. It stays hidden on screen; "
                    "the public key is derived locally with wg pubkey."
                ),
                max_length=44,
                password=True,
            )
        if step == "wireguard-public":
            return self._wireguard_public_widgets()
        if step == "wireguard-peer-key":
            return self._input_widgets(
                label="WIREGUARD SERVER PUBLIC KEY",
                value=self.config.wireguard_peer_public_key,
                placeholder="Base64 server public key",
                input_id="wireguard-peer-key-input",
                hint="Paste the public key from the server. Never enter the server private key.",
                max_length=44,
            )
        if step == "wireguard-endpoint":
            return self._input_widgets(
                label="WIREGUARD SERVER ENDPOINT",
                value=self.config.wireguard_endpoint,
                placeholder="vpn.example.com:51820",
                input_id="wireguard-endpoint-input",
                hint="Use hostname:port, IPv4:port, or [IPv6]:port.",
                max_length=260,
            )
        if step == "wireguard-address":
            return self._input_widgets(
                label="THIS MACHINE'S TUNNEL ADDRESS",
                value=self.config.wireguard_address,
                placeholder="10.20.0.2/24",
                input_id="wireguard-address-input",
                hint=(
                    "Enter the address assigned to this peer, including its CIDR prefix. "
                    "Only that subnet is routed through WireGuard; the default route stays on the physical network."
                ),
                max_length=64,
            )
        if step == "wireguard-test":
            return self._wireguard_test_widgets()
        if step == "review":
            return self._review_widgets()
        raise ValueError(f"Unknown installer step: {step}")

    @staticmethod
    def _copy_for_step(step: str) -> tuple[str, str]:
        copies = {
            "disk": (
                "Choose the installation drive",
                "Only safe, writable drives are shown. Select one with the arrows and press Enter.",
            ),
            "data-policy": (
                "Keep or clean persistent data?",
                "Keep is selected first. It preserves the existing filesystem "
                "while reinstalling OKO.",
            ),
            "disk-confirm": (
                "Confirm the target drive",
                "This is the first safety check. No data is written at this point.",
            ),
            "hostname": (
                "Name this server",
                "This name identifies the installed machine on your network and at the console.",
            ),
            "password": (
                "Protect the admin account",
                "Choose the password for the fixed admin account used for local and remote login after installation.",
            ),
            "password-confirm": (
                "Type the password once more",
                "A second entry catches typing mistakes before the target system is configured.",
            ),
            "timezone-region": (
                "Choose the server timezone",
                "Start with UTC or a geographic region. Use only the arrows and Enter.",
            ),
            "timezone-city": (
                "Choose the nearest timezone city",
                "The selected zone controls system time display and scheduled jobs.",
            ),
            "interface": (
                "Choose the network connection",
                "The installed profile is matched to the selected interface hardware address.",
            ),
            "network-mode": (
                "Choose how the server gets an address",
                "DHCP is the straightforward choice. Static mode asks three additional questions.",
            ),
            "address": (
                "Set the static address",
                "Enter the server address with its network prefix, such as 192.168.1.50/24.",
            ),
            "gateway": (
                "Set the default gateway",
                "Enter the router address, or leave this empty for an isolated network.",
            ),
            "dns": (
                "Set the DNS servers",
                "These resolvers are written into the persistent systemd-networkd profile.",
            ),
            "wireguard-choice": (
                "Add a WireGuard tunnel?",
                "WireGuard is optional. Generate a new device key or reuse an existing private key.",
            ),
            "wireguard-private-key": (
                "Paste the existing private key",
                "The key is validated locally and used only to derive this machine's public key.",
            ),
            "wireguard-public": (
                "Register this machine on the server",
                "Add the displayed device public key to the server before testing the tunnel.",
            ),
            "wireguard-peer-key": (
                "Enter the server public key",
                "This authenticates the remote WireGuard peer to the installed machine.",
            ),
            "wireguard-endpoint": (
                "Enter the WireGuard endpoint",
                "The endpoint must be reachable over the live physical network for the test to succeed.",
            ),
            "wireguard-address": (
                "Set this machine's tunnel address",
                "Use the address reserved for this peer in the server's WireGuard configuration.",
            ),
            "wireguard-test": (
                "Test the WireGuard connection",
                "The installer creates a temporary interface and requires an authenticated server handshake.",
            ),
            "review": (
                "Review and install",
                "The safe choice is selected first. Move down once and press Enter to begin.",
            ),
        }
        return copies[step]

    async def show_step(self, step: str) -> None:
        if step not in STEP_PHASE or self._navigation_locked:
            return
        self._navigation_locked = True
        try:
            self.current_step = step
            self.page_index = STEP_PHASE[step]
            self.mode = "review" if step == "review" else "wizard"
            title, description_text = self._copy_for_step(step)
            description = self.query_one("#page-description", Static)
            description.styles.display = "block"
            description.update(description_text)
            self.query_one("#page-title", Static).update(title)
            self._clear_error()

            if step == "review":
                self._set_header_mode("FINAL CHECK", "#8b7cf6")
                self.query_one("#page-kicker", Static).update(
                    "FINAL REVIEW  /  TARGET STILL UNCHANGED"
                )
            else:
                self._set_header_mode("GUIDED SETUP", "#8b7cf6")
                position, total = STEP_POSITION[step]
                if self.page_index == 0 and not self.config.existing_data:
                    position, total = {
                        "disk": (1, 2),
                        "disk-confirm": (2, 2),
                    }.get(step, (position, total))
                phase = WIZARD_STEPS[self.page_index].upper()
                self.query_one("#page-kicker", Static).update(
                    f"{self.page_index + 1:02d} {phase}  /  {position:02d} OF {total:02d}"
                )

            await self._replace_content(*self._widgets_for_step(step))
            self._update_rail()
            self._set_action_hints(
                left="CTRL+P  POWER" if step == "disk" else "ESC  BACK",
                context=(
                    "SAFE CHOICE SELECTED FIRST"
                    if step in {"data-policy", "disk-confirm", "review"}
                    else "DISK UNCHANGED"
                ),
                enter=(
                    "ENTER  CONTINUE  >"
                    if step
                    in {
                        "hostname",
                        "password",
                        "password-confirm",
                        "address",
                        "gateway",
                        "dns",
                        "wireguard-private-key",
                        "wireguard-peer-key",
                        "wireguard-endpoint",
                        "wireguard-address",
                    }
                    else "ENTER  SELECT  >"
                ),
                danger=step == "review",
            )
            self.query_one("#keybar", Static).update(
                "TYPE ANSWER     ENTER  CONTINUE     ESC  BACK     F10  CLOSE"
                if step
                in {
                    "hostname",
                    "password",
                    "password-confirm",
                    "address",
                    "gateway",
                    "dns",
                    "wireguard-private-key",
                    "wireguard-peer-key",
                    "wireguard-endpoint",
                    "wireguard-address",
                }
                else "UP/DOWN  CHOOSE     ENTER  SELECT     ESC  BACK     F10  CLOSE"
            )
            if step == "disk" and self.disk_scan_error:
                self._set_error(self.disk_scan_error)
            elif step == "interface" and self.interface_scan_error:
                self._set_error(self.interface_scan_error)
            self.call_after_refresh(self._focus_default)
        finally:
            self._navigation_locked = False

    async def show_page(self, index: int) -> None:
        """Compatibility entry point for the three public setup phases."""

        phase_starts = ("disk", "hostname", "interface")
        index = max(0, min(index, len(phase_starts) - 1))
        await self.show_step(phase_starts[index])

    def _focus_default(self) -> None:
        selectors = {
            "disk": "#disk-list",
            "data-policy": "#data-policy-choice",
            "disk-confirm": "#disk-confirm-choice",
            "hostname": "#hostname-input",
            "password": "#password-input",
            "password-confirm": "#confirm-input",
            "timezone-region": "#timezone-regions",
            "timezone-city": "#timezone-cities",
            "interface": "#network-interface",
            "network-mode": "#network-mode",
            "address": "#address-input",
            "gateway": "#gateway-input",
            "dns": "#dns-input",
            "wireguard-choice": "#wireguard-choice",
            "wireguard-private-key": "#wireguard-private-key-input",
            "wireguard-public": "#wireguard-public-actions",
            "wireguard-peer-key": "#wireguard-peer-key-input",
            "wireguard-endpoint": "#wireguard-endpoint-input",
            "wireguard-address": "#wireguard-address-input",
            "wireguard-test": "#wireguard-test-actions",
            "review": "#review-choice",
        }
        selector = selectors.get(self.current_step)
        if self.mode == "power":
            selector = "#power-actions"
        elif self.mode == "result":
            selector = "#result-actions"
        if selector:
            try:
                self.query_one(selector).focus()
            except Exception:
                pass

    async def _submit_input(self, input_id: str, raw_value: str) -> None:
        self._clear_error()
        value = raw_value.strip()
        if input_id == "hostname-input":
            if not HOSTNAME_RE.fullmatch(value):
                self._set_error(
                    "Use one DNS label of 1-63 letters, numbers, or hyphens."
                )
                return
            self.config.hostname = value
            await self.show_step("password")
        elif input_id == "password-input":
            if len(raw_value) < 8:
                self._set_error("Use at least 8 characters for the password.")
                return
            if "\n" in raw_value or "\r" in raw_value:
                self._set_error("The password cannot contain a line break.")
                return
            self.config.password = raw_value
            self.password_confirmation = ""
            await self.show_step("password-confirm")
        elif input_id == "confirm-input":
            if raw_value != self.config.password:
                self._set_error("The two passwords do not match. Try again.")
                return
            self.password_confirmation = raw_value
            await self.show_step("timezone-region")
        elif input_id == "address-input":
            if "/" not in value:
                self._set_error(
                    "Include a network prefix, for example 192.168.1.50/24."
                )
                return
            try:
                ipaddress.ip_interface(value)
            except ValueError:
                self._set_error(
                    "Enter a valid IPv4 or IPv6 address with its prefix."
                )
                return
            self.config.address = value
            await self.show_step("gateway")
        elif input_id == "gateway-input":
            if value:
                try:
                    gateway = ipaddress.ip_address(value)
                    address = ipaddress.ip_interface(self.config.address)
                except ValueError:
                    self._set_error("Enter one valid gateway address, or leave it empty.")
                    return
                if gateway.version != address.version:
                    self._set_error(
                        "The gateway and static address must use the same IP version."
                    )
                    return
            self.config.gateway = value
            await self.show_step("dns")
        elif input_id == "dns-input":
            issue = validate_network(
                "static",
                self.config.address,
                self.config.gateway,
                value,
            )
            if issue:
                self._set_error(issue)
                return
            self.config.dns = value
            await self.show_step("wireguard-choice")
        elif input_id == "wireguard-private-key-input":
            issue = validate_wireguard_key(value, "Private key")
            if issue:
                self._set_error(issue)
                return
            try:
                public_key = await asyncio.to_thread(derive_wireguard_public_key, value)
            except InstallError as exc:
                self._set_error(str(exc))
                return
            self.config.wireguard_enabled = True
            self.config.wireguard_key_source = "existing"
            self.config.wireguard_private_key = value
            self.config.wireguard_public_key = public_key
            self.config.wireguard_tested = False
            self.wireguard_test_message = "Not tested yet"
            await self.show_step("wireguard-public")
        elif input_id == "wireguard-peer-key-input":
            issue = validate_wireguard_key(value, "Server public key")
            if issue:
                self._set_error(issue)
                return
            if value == self.config.wireguard_public_key:
                self._set_error(
                    "Server public key must be different from this machine's public key."
                )
                return
            self.config.wireguard_peer_public_key = value
            self.config.wireguard_tested = False
            await self.show_step("wireguard-endpoint")
        elif input_id == "wireguard-endpoint-input":
            try:
                split_wireguard_endpoint(value)
            except InstallError as exc:
                self._set_error(str(exc))
                return
            self.config.wireguard_endpoint = value
            self.config.wireguard_tested = False
            await self.show_step("wireguard-address")
        elif input_id == "wireguard-address-input":
            try:
                address = ipaddress.ip_interface(value)
            except ValueError:
                self._set_error(
                    "Enter a valid tunnel address with its prefix, for example 10.20.0.2/24."
                )
                return
            self.config.wireguard_address = str(address)
            self.config.wireguard_tested = False
            issue = validate_wireguard(self.config)
            if issue:
                self._set_error(issue)
                return
            await self.show_step("wireguard-test")

    async def _prepare_wireguard_key(self, *, replace: bool = False) -> bool:
        if (
            not replace
            and validate_wireguard_key(
                self.config.wireguard_private_key, "Device private key"
            )
            is None
            and validate_wireguard_key(
                self.config.wireguard_public_key, "Device public key"
            )
            is None
        ):
            return True
        self._clear_error()
        self._set_action_hints(
            left="ESC  BACK",
            context="GENERATING DEVICE KEY",
            enter="PLEASE WAIT",
        )
        try:
            private_key, public_key = await asyncio.to_thread(
                generate_wireguard_keypair
            )
        except InstallError as exc:
            self._set_error(str(exc))
            return False
        self.config.wireguard_key_source = "generate"
        self.config.wireguard_private_key = private_key
        self.config.wireguard_public_key = public_key
        self.config.wireguard_tested = False
        self.wireguard_test_message = "Not tested yet"
        return True

    async def _test_wireguard(self) -> None:
        if self.wireguard_testing:
            return
        self.wireguard_testing = True
        self._clear_error()
        self._set_action_hints(
            left="ESC  BACK",
            context="WAITING FOR SERVER HANDSHAKE",
            enter="TESTING...",
        )
        error: str | None = None
        try:
            message = await asyncio.to_thread(test_wireguard_tunnel, self.config)
            self.config.wireguard_tested = True
            self.wireguard_test_message = message
        except InstallError as exc:
            self.config.wireguard_tested = False
            self.wireguard_test_message = "Handshake failed"
            error = str(exc)
        finally:
            self.wireguard_testing = False
        await self.show_step("wireguard-test")
        if error:
            self._set_error(error)

    async def _go_back(self) -> None:
        if self.mode == "progress":
            self._set_error("Installation is active and cannot be interrupted safely.")
            return
        if self.mode == "power":
            await self._restore_after_power_options()
            return
        if self.mode == "result":
            if not self.install_success:
                await self.show_review()
            return
        if self.mode == "review":
            await self.show_step(
                "wireguard-test"
                if self.config.wireguard_enabled
                else "wireguard-choice"
            )
            return

        previous = {
            "disk-confirm": "disk",
            "data-policy": "disk",
            "hostname": "disk-confirm",
            "password": "hostname",
            "password-confirm": "password",
            "timezone-region": "password-confirm",
            "timezone-city": "timezone-region",
            "interface": "timezone-region",
            "network-mode": "interface",
            "address": "network-mode",
            "gateway": "address",
            "dns": "gateway",
            "wireguard-private-key": "wireguard-choice",
            "wireguard-public": "wireguard-choice",
            "wireguard-peer-key": "wireguard-public",
            "wireguard-endpoint": "wireguard-peer-key",
            "wireguard-address": "wireguard-endpoint",
            "wireguard-test": "wireguard-address",
        }.get(self.current_step)
        if self.current_step == "wireguard-choice":
            previous = "network-mode" if self.config.network_mode == "dhcp" else "dns"
        elif (
            self.current_step == "wireguard-public"
            and self.config.wireguard_key_source == "existing"
        ):
            previous = "wireguard-private-key"
        if self.current_step == "disk-confirm" and self.config.existing_data:
            previous = "data-policy"
        if previous:
            await self.show_step(previous)
        else:
            await self._show_power_options()

    async def show_review(self) -> None:
        if not self.config.disk or not self.config.interface:
            self._set_error("Storage and network hardware must be selected first.")
            return
        issue = validate_wireguard(self.config)
        if issue:
            self._set_error(issue)
            return
        await self.show_step("review")

    async def _show_power_options(self) -> None:
        if self.mode == "progress":
            self._set_error("Installation is active and cannot be interrupted safely.")
            return
        if self.mode != "power":
            self._power_return_mode = self.mode
            self._power_return_step = self.current_step
        self.mode = "power"
        self._set_header_mode("POWER OPTIONS", "#fbbf24")
        self.query_one("#page-kicker", Static).update("SAFE EXIT")
        self.query_one("#page-title", Static).update("Power off the live installer?")
        description = self.query_one("#page-description", Static)
        description.styles.display = "block"
        description.update(
            "Your current answers remain in memory if you return to setup."
        )
        self._clear_error()
        choices = OptionList(
            Option(
                "[bold]KEEP SETTING UP[/bold]\nSAFE - return to the current screen",
                id="power-stay",
            ),
            Option(
                "[bold]POWER OFF THIS MACHINE[/bold]\nStop and shut down",
                id="power-off",
            ),
            id="power-actions",
            classes="choice-list",
        )
        choices.highlighted = 0
        await self._replace_content(
            self._stage(
                Static(
                    "Nothing is written before the final install confirmation.",
                    classes="notice-card",
                ),
                choices,
            )
        )
        self._set_action_hints(
            left="ESC  STAY",
            context="SAFE CHOICE SELECTED FIRST",
            enter="ENTER  SELECT  >",
        )
        self.query_one("#keybar", Static).update(
            "UP/DOWN  CHOOSE     ENTER  SELECT     ESC  STAY     F10  CLOSE"
        )
        self.call_after_refresh(self._focus_default)

    async def _restore_after_power_options(self) -> None:
        return_mode = self._power_return_mode
        if return_mode == "review":
            await self.show_review()
        elif return_mode == "result":
            await self.show_result(self.install_success, self.result_message)
        else:
            await self.show_step(self._power_return_step)

    def _power_off(self) -> None:
        try:
            result = subprocess.run(
                ["systemctl", "poweroff"],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            self._set_error(f"Could not request power off: {exc}")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self._set_error(detail or "The power-off request failed.")

    async def show_progress(self) -> None:
        self.mode = "progress"
        self.current_percent = 0
        self.logs = []
        self._set_header_mode("INSTALLING", "#fbbf24")
        self.query_one("#page-kicker", Static).update("A/B SYSTEM DEPLOYMENT")
        self.query_one("#page-title", Static).update("Installing OKO Server")
        self.query_one("#page-description", Static).update(
            "The signed-off rootfs bundle is being verified, partitioned, installed, and configured."
        )
        self._clear_error()
        await self._replace_content(
            self._stage(
                Static("000%", id="progress-number"),
                ProgressBar(total=100, show_eta=False, id="install-progress"),
                Static("Preparing installation...", id="install-stage"),
                RichLog(highlight=False, markup=True, wrap=True, id="install-log"),
                full=True,
            )
        )
        self._update_rail()
        self.query_one("#actionbar", Horizontal).styles.display = "none"
        self.query_one("#keybar", Static).update(
            "DO NOT POWER OFF OR REMOVE MEDIA     F10  CLOSE INSTALLER"
        )

    def _receive_progress(self, percent: int, message: str) -> None:
        if percent >= 0:
            self.current_percent = percent
            self.query_one("#install-progress", ProgressBar).progress = percent
            self.query_one("#progress-number", Static).update(f"{percent:03d}%")
            self.query_one("#install-stage", Static).update(escape(message))
        self.logs.append(message)
        self.logs = self.logs[-80:]
        rendered = (
            f"[#60758d]{escape(message)}[/#60758d]"
            if message.startswith("$ ")
            else escape(message)
        )
        self.query_one("#install-log", RichLog).write(rendered)

    async def start_installation(self) -> None:
        if self.mode != "review" or not self.config.disk or not self.config.interface:
            self._set_error("Return to setup and complete every required choice.")
            return
        await self.show_progress()
        self.run_worker(self._run_engine(), name="oko-install", exclusive=True)

    async def _run_engine(self) -> None:
        def reporter(percent: int, message: str) -> None:
            self.call_from_thread(self._receive_progress, percent, message)

        try:
            await asyncio.to_thread(InstallerEngine(self.config, reporter).install)
        except Exception as exc:
            await self.show_result(False, str(exc))
        else:
            await self.show_result(True, "The target disk is ready.")

    async def show_result(self, success: bool, message: str) -> None:
        self.mode = "result"
        self.install_success = success
        self.result_message = message
        color = "#5eead4" if success else "#fb7185"
        self._set_header_mode("COMPLETE" if success else "FAILED", color)
        self.query_one("#page-kicker", Static).update("INSTALLATION RESULT")
        self.query_one("#page-title", Static).update(
            "Installation complete" if success else "Installation failed"
        )
        self.query_one("#page-description", Static).update(
            "OKO is ready for its first boot."
            if success
            else "The installer stopped safely. Review the message below before trying again."
        )
        self._clear_error()
        if success:
            detail = (
                f"{escape(message)}\n\n"
                "Remove the ISO or USB installation media, then reboot. "
                "SSH, Wetty, and the tty1/ttyS0 dashboards will start "
                "automatically."
            )
        else:
            tail = "\n".join(escape(line) for line in self.logs[-6:])
            detail = f"{escape(message)}"
            if tail:
                detail += f"\n\nLast installer messages:\n{tail}"
        actions = OptionList(
            *(
                (
                    Option(
                        "[bold]REBOOT INTO OKO[/bold]\n"
                        "Remove the medium, then start the server",
                        id="result-reboot",
                    ),
                    Option(
                        "[bold]POWER OFF[/bold]\nShut down and remove the medium",
                        id="result-poweroff",
                    ),
                )
                if success
                else (
                    Option(
                        "[bold]REVIEW AND TRY AGAIN[/bold]\n"
                        "Rescan drives and restart setup",
                        id="result-retry",
                    ),
                    Option(
                        "[bold]POWER OFF[/bold]\nShut down the live installer",
                        id="result-poweroff",
                    ),
                )
            ),
            id="result-actions",
            classes="choice-list",
        )
        actions.highlighted = 0
        await self._replace_content(
            self._stage(
                Static(
                    "[bold]OKO SERVER IS READY[/bold]"
                    if success
                    else "[bold]INSTALLATION NEEDS ATTENTION[/bold]",
                    id="result-badge",
                    classes="success" if success else "failure",
                ),
                Static(detail, id="result-detail"),
                actions,
                full=True,
            )
        )
        self._update_rail()
        self._set_action_hints(
            left="CTRL+P  POWER",
            context="INSTALLATION COMPLETE" if success else "NO PROCESS RUNNING",
            enter="ENTER  SELECT  >",
        )
        self.query_one("#keybar", Static).update(
            "UP/DOWN  CHOOSE     ENTER  SELECT     F10  CLOSE"
            if success
            else "UP/DOWN  CHOOSE     ENTER  SELECT     F10  CLOSE"
        )
        self.call_after_refresh(self._focus_default)

    def _reboot(self) -> None:
        try:
            result = subprocess.run(
                ["systemctl", "reboot"],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            self._set_error(f"Could not request reboot: {exc}")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self._set_error(detail or "The reboot request failed.")

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        option_id = str(event.option.id) if event.option.id is not None else ""
        if event.option_list.id == "disk-list":
            disk = self.disk_option_map.get(option_id)
            if disk:
                self.query_one("#disk-details", Static).update(
                    escape(
                        f"{disk.path}  /  {disk.size_text}  /  {disk.model}. "
                        "Selection does not write anything yet."
                    )
                )
        elif event.option_list.id == "network-interface":
            interface = self.interface_option_map.get(option_id)
            if interface:
                self.query_one("#interface-details", Static).update(
                    escape(
                        f"{interface.name}  /  {interface.mac}  /  link {interface.state}. "
                        "The installed profile follows this hardware address."
                    )
                )

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        option_id = str(event.option.id) if event.option.id is not None else ""
        list_id = event.option_list.id
        if list_id == "disk-list":
            disk = self.disk_option_map.get(option_id)
            if disk:
                self.config.disk = disk
                if await self._inspect_selected_data():
                    await self.show_step(
                        "data-policy"
                        if self.config.existing_data
                        else "disk-confirm"
                    )
            elif option_id == "disk-rescan":
                await self._scan_disks()
                await self.show_step("disk")
        elif list_id == "data-policy-choice":
            if option_id == "data-keep":
                if (
                    self.config.existing_data
                    and self.config.existing_data.fstype == "ext4"
                ):
                    self.config.data_policy = "keep"
                    await self.show_step("disk-confirm")
            elif option_id == "data-clean":
                self.config.data_policy = "clean"
                await self.show_step("disk-confirm")
        elif list_id == "disk-confirm-choice":
            if option_id == "disk-confirm-back":
                await self.show_step(
                    "data-policy" if self.config.existing_data else "disk"
                )
            elif option_id == "disk-confirm-use":
                await self.show_step("hostname")
        elif list_id == "timezone-regions":
            choice = self.timezone_option_map.get(option_id, "")
            if choice == "UTC":
                self.config.timezone = "UTC"
                self.timezone_region = None
                await self.show_step("interface")
            elif choice:
                self.timezone_region = choice
                await self.show_step("timezone-city")
        elif list_id == "timezone-cities":
            zone = self.timezone_option_map.get(option_id, "")
            if zone:
                self.config.timezone = zone
                await self.show_step("interface")
        elif list_id == "network-interface":
            interface = self.interface_option_map.get(option_id)
            if interface:
                self.config.interface = interface
                await self.show_step("network-mode")
            elif option_id == "interface-rescan":
                await self._scan_interfaces()
                await self.show_step("interface")
        elif list_id == "network-mode":
            if option_id == "mode-dhcp":
                self.config.network_mode = "dhcp"
                await self.show_step("wireguard-choice")
            elif option_id == "mode-static":
                self.config.network_mode = "static"
                await self.show_step("address")
        elif list_id == "wireguard-choice":
            if option_id == "wireguard-disabled":
                self.config.wireguard_enabled = False
                self.config.wireguard_key_source = "generate"
                self.config.wireguard_private_key = ""
                self.config.wireguard_public_key = ""
                self.config.wireguard_peer_public_key = ""
                self.config.wireguard_endpoint = ""
                self.config.wireguard_address = ""
                self.config.wireguard_tested = False
                self.wireguard_test_message = "Not tested yet"
                await self.show_review()
            elif option_id == "wireguard-generate":
                replace = self.config.wireguard_key_source != "generate"
                self.config.wireguard_enabled = True
                self.config.wireguard_key_source = "generate"
                if await self._prepare_wireguard_key(replace=replace):
                    await self.show_step("wireguard-public")
            elif option_id == "wireguard-existing":
                if self.config.wireguard_key_source != "existing":
                    self.config.wireguard_private_key = ""
                    self.config.wireguard_public_key = ""
                self.config.wireguard_enabled = True
                self.config.wireguard_key_source = "existing"
                self.config.wireguard_tested = False
                self.wireguard_test_message = "Not tested yet"
                await self.show_step("wireguard-private-key")
        elif list_id == "wireguard-public-actions":
            if option_id == "wireguard-public-continue":
                await self.show_step("wireguard-peer-key")
            elif option_id == "wireguard-public-regenerate":
                if await self._prepare_wireguard_key(replace=True):
                    await self.show_step("wireguard-public")
            elif option_id == "wireguard-public-change-private":
                await self.show_step("wireguard-private-key")
        elif list_id == "wireguard-test-actions":
            if option_id == "wireguard-test-run":
                await self._test_wireguard()
            elif option_id == "wireguard-test-continue":
                await self.show_review()
        elif list_id == "review-choice":
            if option_id == "review-back":
                await self._go_back()
            elif option_id == "review-install":
                await self.start_installation()
        elif list_id == "power-actions":
            if option_id == "power-stay":
                await self._restore_after_power_options()
            elif option_id == "power-off":
                self._power_off()
        elif list_id == "result-actions":
            if option_id == "result-reboot":
                self._reboot()
            elif option_id == "result-poweroff":
                self._power_off()
            elif option_id == "result-retry":
                await self._scan_hardware()
                await self.show_step("disk")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id:
            await self._submit_input(event.input.id, event.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "password-input":
            return
        strength, color = password_strength(event.value)
        try:
            self.query_one("#password-strength", Static).update(
                f"PASSWORD QUALITY  /  [{color}]{strength}[/{color}]"
            )
        except Exception:
            pass

    def on_key(self, event: Key) -> None:
        """Recover focus with an arrow; normal widgets handle movement themselves."""

        if event.key in {"up", "down"} and self.focused is None:
            self._focus_default()

    async def action_go_back(self) -> None:
        await self._go_back()

    async def action_power_options(self) -> None:
        await self._show_power_options()

    def action_quit_installer(self) -> None:
        """Close only the installer application and return success."""

        self.exit()


def main() -> int:
    if os.geteuid() != 0:
        print("oko-installer must run as root", file=sys.stderr)
        return 1
    OKOInstaller().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


