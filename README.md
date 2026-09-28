# OKO

Open Source AI Operating System. Maximum AI performance on your hardware.

## Get OKO

**Download a pre-built image:** https://oko.satisfanly.com

**Build from source:**

```bash
git clone --recursive https://github.com/satisfanly/OKO.git
cd OKO
source poky/openembedded-core/oe-init-build-env env/genericx86-64
bitbake oko-installer-image
```

Outputs in `env/genericx86-64/tmp/deploy/images/genericx86-64/`:
- `oko-installer-image-genericx86-64.rootfs.iso` — bootable installer ISO
- `oko-target-image-genericx86-64.rootfs.wic.zst` — disk image (used by installer)

## Install

1. Write the installer ISO to a USB drive and boot from it.
2. The installer TUI walks you through:
   - Destination disk selection (with explicit ERASE confirmation)
   - Server name, admin user/password, timezone
   - Network configuration (DHCP or static)
3. The installer writes the disk image, expands the root partition, and reboots.

After installation:
- SSH is enabled
- tty1 shows system status (CPU, GPU, RAM, network)
- tty2 provides local login

## Update

OKO uses A/B partitioning for safe in-place updates. Run as root:

```bash
oko-update
```

This checks for the latest release, downloads it to the inactive slot, preserves your users and configuration, switches the boot slot, and reboots.

Options:
- `--check` — check for updates without installing
- `--yes` — skip confirmation prompt
- `--force` — reinstall even if already on the latest version
- `--no-reboot` — don't reboot after update

The update endpoint is configured in `/etc/oko-update.conf`.
