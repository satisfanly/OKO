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

SUMMARY = "AMD Ryzen SMU kernel driver"
DESCRIPTION = "Kernel driver exposing AMD Ryzen System Management Unit interfaces"
HOMEPAGE = "https://github.com/amkillam/ryzen_smu"

LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRC_URI = " \
    git://github.com/amkillam/ryzen_smu.git;protocol=https;branch=main \
    file://ryzen-smu-global-userspace-mutex.patch \
    "
SRCREV = "${AUTOREV}"

PV = "0.1.7+git"

inherit module

COMPATIBLE_HOST = "x86_64.*-linux"

KERNEL_MODULE_AUTOLOAD += "ryzen_smu"

do_compile() {
    unset CFLAGS CPPFLAGS CXXFLAGS LDFLAGS

    oe_runmake \
        -C ${STAGING_KERNEL_DIR} \
        O=${STAGING_KERNEL_BUILDDIR} \
        M=${S} \
        CC="${KERNEL_CC}" \
        LD="${KERNEL_LD}" \
        AR="${KERNEL_AR}" \
        OBJCOPY="${KERNEL_OBJCOPY}" \
        STRIP="${KERNEL_STRIP}" \
        modules
}

do_install() {
    install -d \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra

    install -m 0644 \
        ${S}/ryzen_smu.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/ryzen_smu.ko
}
