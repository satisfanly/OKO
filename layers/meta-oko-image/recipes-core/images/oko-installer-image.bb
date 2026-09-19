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

SUMMARY = "OKO bootable Textual USB installer"
DESCRIPTION = "Raw GPT BIOS/UEFI USB image that provisions the OKO A/B layout from the release bundle"
LICENSE = "MIT"

IMAGE_INSTALL = " \
    packagegroup-oko-image-common \
    packagegroup-oko-base \
    oko-installer \
"

# volatile-binds mounts tmpfs to such directories as /run /tmp etc, so this prevents
# systemd-timesyncd.service: Failed to set up special execution directory in /var/lib: Read-only file system
IMAGE_INSTALL:append = " volatile-binds"
# This is specific feature, because WKS ignores --fsoptions="ro" for "/" part
IMAGE_FEATURES = "read-only-rootfs"

# USB disk image
IMAGE_FSTYPES = "wic"
WKS_FILE = "files/oko-installer-usb.wks.in"

OKO_TARGET_BUNDLE = "${DEPLOY_DIR_IMAGE}/oko-update-${MACHINE}.rootfs.tar.zst"
do_image[depends] += "oko-target-image:do_image_complete coreutils-native:do_populate_sysroot"

# Put the installation payload directly into the USB installer's rootfs.
populate_installer_payload() {
    if [ ! -s "${OKO_TARGET_BUNDLE}" ]; then
        bbfatal "Missing target update bundle: ${OKO_TARGET_BUNDLE}"
    fi

    install -d "${IMAGE_ROOTFS}/install"

    install -m 0644 \
        "${OKO_TARGET_BUNDLE}" \
        "${IMAGE_ROOTFS}/install/oko-rootfs.tar.zst"

    ${STAGING_BINDIR_NATIVE}/sha256sum \
        "${OKO_TARGET_BUNDLE}" \
        > "${IMAGE_ROOTFS}/install/oko-rootfs.tar.zst.sha256"

    chmod 0644 \
        "${IMAGE_ROOTFS}/install/oko-rootfs.tar.zst.sha256"
}

inherit core-image image-buildinfo extrausers

IMAGE_PREPROCESS_COMMAND:append = " populate_installer_payload;"

# USB installer only: root / root.
# Installed OKO still locks root.
EXTRA_USERS_PARAMS = " \
    usermod -p '\$6\$oko-installer\$65DEqn/CInEHUFD6V8sY2sjGWp8z2WOaolNcUslM8MSJWBeYbHqI30Sr7XOlWFnJ6tJhoWsYPFDtRclYWVMbk.' root; \
"
