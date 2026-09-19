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

SUMMARY = "OKO standart image admin utils"
LICENSE = "MIT"
PACKAGE_ARCH = "${MACHINE_ARCH}"

inherit packagegroup

RDEPENDS:${PN} = " \
    packagegroup-base \
    packagegroup-core-boot \
    python3-core \
    python3-modules \
    tzdata \
    bash \
    coreutils \
    util-linux \
    util-linux-lsblk \
    util-linux-mount \
    util-linux-swaponoff \
    util-linux-umount \
    systemd \
    udev \
    iproute2 \
    kmod \
    shadow \
    sudo \
    tzdata \
    ca-certificates \
    apt \
    dpkg \
    openssh \
    openssh-sshd \
    openssh-sftp-server \
    systemd-networkd \
    kernel-image \
    kernel-modules \
    bmaptool \
    e2fsprogs \
    e2fsprogs-mke2fs \
    dosfstools \
    e2fsprogs-e2fsck \
    e2fsprogs-resize2fs \
    parted \
    zstd \
    tar \
    wireguard-tools \
    iptables \
    libdrm-tests \
    btop \
    htop \
    iproute2-ss \
"
