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

SUMMARY = "Safe OKO A/B system updater"
DESCRIPTION = "Installs a rootfs and kernel nto the inactive OKO slot"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Note that oko-update.conf provided by bsp layer
SRC_URI = " \
    file://oko-update.py \
    file://oko-update.conf \
"

S = "${UNPACKDIR}"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${UNPACKDIR}/oko-update.py \
        ${D}${sbindir}/oko-update

    install -d ${D}${sysconfdir}
    install -m 0644 ${UNPACKDIR}/oko-update.conf \
        ${D}${sysconfdir}/oko-update.conf
}

CONFFILES:${PN} = "${sysconfdir}/oko-update.conf"

RDEPENDS:${PN} = " \
    python3-core \
    python3-modules \
    ca-certificates \
    e2fsprogs-mke2fs \
    util-linux-mount \
    util-linux-umount \
    util-linux-sfdisk \
    tar \
    zstd \
    systemd \
"
