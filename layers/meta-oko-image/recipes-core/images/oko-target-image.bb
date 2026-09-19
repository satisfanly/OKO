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

SUMMARY = "OKO headless server target root filesystem"
DESCRIPTION = "A/B-ready systemd server rootfs with APT, SSH, web terminal and dashboards"
LICENSE = "MIT"

IMAGE_INSTALL = " \
    packagegroup-oko-image-common \
    packagegroup-oko-base \
    oko-console \
    oko-update \
    packagegroup-oko-ai-inference \
    packagegroup-oko-ai-utils \
"
IMAGE_FEATURES += "package-management ssh-server-openssh"

IMAGE_FSTYPES = "tar.zst tar.gz"
IMAGE_ROOTFS_EXTRA_SPACE = "524288"

inherit core-image image-buildinfo extrausers

# ---------------------------------------------------------------------------
# Installed root filesystem
# ---------------------------------------------------------------------------

# The installer extracts this rootfs into slot A. oko-update rewrites only the
# root entry when deploying the same rootfs into slot B.
add_oko_mounts() {
    install -d ${IMAGE_ROOTFS}/boot ${IMAGE_ROOTFS}/home
    install -d ${IMAGE_ROOTFS}${sysconfdir}
    touch ${IMAGE_ROOTFS}${sysconfdir}/fstab

    sed -i \
        -e '\|[[:space:]]/[[:space:]]|d' \
        -e '\|[[:space:]]/boot[[:space:]]|d' \
        -e '\|[[:space:]]/home[[:space:]]|d' \
        ${IMAGE_ROOTFS}${sysconfdir}/fstab

    printf '%s\n' \
        'PARTLABEL=oko-root-a / ext4 defaults 0 1' \
        'LABEL=OKO_BOOT /boot vfat defaults 0 2' \
        'PARTLABEL=oko-data /home ext4 defaults 0 2' \
        >> ${IMAGE_ROOTFS}${sysconfdir}/fstab
}

ROOTFS_POSTPROCESS_COMMAND:append = " add_oko_mounts;"

# The installer creates the named administrator and sets its password.
# Keep UID 0 locked and inaccessible through SSH.
EXTRA_USERS_PARAMS = "usermod -L root;"


# ---------------------------------------------------------------------------
# Update bundle
#
# The update bundle is the exact target rootfs archive plus the matching
# deployed kernel as a transport-only file.
# ---------------------------------------------------------------------------

OKO_UPDATE_LINK_NAME = "oko-update${IMAGE_MACHINE_SUFFIX}${IMAGE_NAME_SUFFIX}"
OKO_UPDATE_NAME = "${OKO_UPDATE_LINK_NAME}${IMAGE_VERSION_SUFFIX}"

OKO_UPDATE_BUNDLE = "${OKO_UPDATE_NAME}.tar.zst"
OKO_UPDATE_BUNDLE_LINK = "${OKO_UPDATE_LINK_NAME}.tar.zst"

do_image_update_bundle[depends] += " \
    virtual/kernel:do_deploy \
    coreutils-native:do_populate_sysroot \
    zstd-native:do_populate_sysroot \
"

do_image_update_bundle() {
    rootfs="${IMGDEPLOYDIR}/${IMAGE_NAME}.tar.zst"
    kernel="${DEPLOY_DIR_IMAGE}/${KERNEL_IMAGETYPE}"

    archive="${WORKDIR}/oko-update-rootfs.tar"
    payload="${WORKDIR}/oko-update-payload"

    if [ ! -s "$rootfs" ]; then
        bbfatal "Missing target rootfs archive: $rootfs"
    fi

    if [ ! -s "$kernel" ]; then
        bbfatal "Missing deployed kernel: $kernel"
    fi

    rm -rf "$payload"
    rm -f "$archive"

    install -d "$payload/usr/lib/oko-update/boot"

    install -m 0644 \
        "$kernel" \
        "$payload/usr/lib/oko-update/boot/bzImage"

    ${STAGING_BINDIR_NATIVE}/touch \
        -d "@${SOURCE_DATE_EPOCH}" \
        "$payload/usr/lib/oko-update/boot/bzImage"

    # Expand the normal Yocto tar.zst rootfs.
    ${STAGING_BINDIR_NATIVE}/zstd \
        -f -q -d -c \
        "$rootfs" \
        > "$archive"

    # Append the kernel as a transport-only object.
    tar \
        --append \
        --file="$archive" \
        --format=gnu \
        --numeric-owner \
        --owner=0 \
        --group=0 \
        --mtime="@${SOURCE_DATE_EPOCH}" \
        -C "$payload" \
        ./usr/lib/oko-update/boot/bzImage

    # Recompress as the publishable update bundle.
    ${STAGING_BINDIR_NATIVE}/zstd \
        -q -T0 -10 -f \
        "$archive" \
        -o "${IMGDEPLOYDIR}/${OKO_UPDATE_BUNDLE}"

    ln -sfn \
        "${OKO_UPDATE_BUNDLE}" \
        "${IMGDEPLOYDIR}/${OKO_UPDATE_BUNDLE_LINK}"
}

addtask image_update_bundle after do_image_tar before do_image_complete
