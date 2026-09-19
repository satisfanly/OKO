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

SUMMARY = "OKO APU power and fan controller"
DESCRIPTION = "Continuously manages Ryzen SMU limits and AXB35 fan cooling policy"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://oko-smu-control \
    file://oko-smu-control.service \
"

inherit systemd

RDEPENDS:${PN} += " \
    ryzenadj \
    ec-su-axb35 \
"

SYSTEMD_SERVICE:${PN} = "oko-smu-control.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 \
        ${UNPACKDIR}/oko-smu-control \
        ${D}${sbindir}/oko-smu-control

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 \
        ${UNPACKDIR}/oko-smu-control.service \
        ${D}${systemd_system_unitdir}/oko-smu-control.service
}

FILES:${PN} += " \
    ${sbindir}/oko-smu-control \
    ${systemd_system_unitdir}/oko-smu-control.service \
"
