# OKO Yocto 6.0 workspace

**To download already built image - navigate to https://oko.satisfanly.com**

This workspace builds two headless x86-64 deliverables with Yocto 6.0
Wrynose:

- `oko-target-image`: a complete `wic.zst` disk image plus bmap,
  target kernel, boot initramfs, BIOS and UEFI bootloaders, systemd, OpenSSH,
  APT, and Yocto-generated Debian-format packages.
- `oko-installer-image`: a live ISO with a three-page TUI that writes
  and verifies the target WIC, expands its root partition, and configures the
  installed system.

The installed userspace is an OKO/Yocto distribution, not Debian. APT can
install packages produced by a compatible OKO build. Debian and Ubuntu
repositories are not ABI-compatible with it.

## Build

```bash
git submodule init
git submodule update --recursive --init
bash # Enter bash (requirement of oe-init-build-env)
source poky/openembedded-core/oe-init-build-env env/genericx86-64
bitbake oko-installer-image
```

Primary outputs are written under
`env/genericx86-64/tmp/deploy/images/genericx86-64/`:

- `oko-target-image-genericx86-64.rootfs.wic.zst`
- `oko-target-image-genericx86-64.rootfs.wic.bmap`
- `oko-installer-image-genericx86-64.rootfs.iso`

Target `tar.zst` and `tar.gz` files are retained as
debugging/recovery artifacts. The installer uses the WIC and bmap, not those
rootfs archives.

## Installation

The installer asks for:

1. Destination disk, followed by an explicit `ERASE` confirmation.
2. Server name, sudo administrator name/password, and timezone.
3. Installed interface and DHCP or static network configuration.

It clears stale partition metadata, uses bmaptool to write and verify the
compressed WIC, expands the `oko-root` partition and ext4 filesystem,
then mounts only the installed root. It changes hostname, timezone,
administrator credentials, machine ID, SSH host keys, and a MAC-matched
systemd-networkd profile.

It does not rebuild the partition table, kernel, initramfs, bootloader, or
fstab. Those remain the coherent artifacts produced by WIC.

The current image geometry supports disks with 512-byte logical sectors,
including 512e disks. The installer rejects native 4Kn disks.

## Installed system

- OpenSSH is enabled.
- The configured administrator has password-protected sudo access.
- UID 0 remains named `root`, password-locked, and unavailable over
  SSH.
- tty1 shows CPU, available GPU usage counters, RAM, network state.
- tty2 provides a normal local login.

## Tests

The repository has no general-purpose `scripts/` directory. Actual
tests live under `tests/`.

Run unit and workspace-integrity tests without the upstream checkouts:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

After cloning the layers, test BitBake configuration and parsing:

```bash
tests/bitbake-parse
```

After building, boot the installer with a disposable 20 GiB QEMU disk:

```bash
tests/boot-installer-qemu
```

Complete an installation in that VM, shut it down, and verify that the
installed disk boots with SSH:

```bash
tests/e2e-installed-qemu
```

## Adding packages

Add always-installed packages to
`layers/meta-oko/recipes-core/packagegroups/packagegroup-oko-target.bb`.
For later installation, publish the compatible contents of
`env/genericx86-64/tmp/deploy/deb/` and configure it as the target's
APT feed. See [package-feed.md](docs/package-feed.md).

The design and installer mutation boundary are documented in
[architecture.md](docs/architecture.md).
