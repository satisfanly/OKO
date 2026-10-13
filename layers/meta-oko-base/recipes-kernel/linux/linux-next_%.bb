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

SUMMARY = "Linux kernel linux-next"
DESCRIPTION = "Linux kernel from the linux-next integration tree"

LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

DEPENDS += "elfutils-native"

inherit kernel

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI = " \
    git://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git;protocol=https;tag=next-20261002;nobranch=1 \
    file://oko-networking.cfg \
    file://oko-vt.cfg \
    file://oko-perfomance-common.cfg \
    file://oko-storage.cfg \
    "

SRCREV = "f0406245cb9855e6318335a8a223551354291a46"

PV = "7.3.0-rc5-next-20261002"

# linux-next source tree
S = "${UNPACKDIR}/linux-next-${PV}"

KERNEL_CONFIG_FRAGMENTS = " \
    oko-networking.cfg \
    oko-vt.cfg \
    oko-perfomance-common.cfg \
    oko-storage.cfg \
    "

do_configure:append() {
    oe_runmake -C ${S} O=${B} x86_64_defconfig

    CONFIG_FRAGMENTS=""

    for fragment in ${KERNEL_CONFIG_FRAGMENTS}; do
        CONFIG_FRAGMENTS="${CONFIG_FRAGMENTS} ${UNPACKDIR}/${fragment}"
    done

    ${S}/scripts/kconfig/merge_config.sh \
        -m \
        ${B}/.config \
        ${CONFIG_FRAGMENTS}

    oe_runmake -C ${S} O=${B} olddefconfig </dev/null
}

