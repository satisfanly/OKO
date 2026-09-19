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

SUMMARY = "OKO base security and administrator policy"
DESCRIPTION = "Defines the sudo administrator group and hardened SSH defaults"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://oko-admin.sudoers \
    file://20-oko-password.conf \
"

inherit useradd

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "--system sudo"

do_install() {
    install -d ${D}${sysconfdir}/sudoers.d
    install -m 0440 ${UNPACKDIR}/oko-admin.sudoers \
        ${D}${sysconfdir}/sudoers.d/oko-admin

    install -d ${D}${sysconfdir}/ssh/sshd_config.d
    install -m 0644 ${UNPACKDIR}/20-oko-password.conf \
        ${D}${sysconfdir}/ssh/sshd_config.d/20-oko-password.conf
}

FILES:${PN} = " \
    ${sysconfdir}/sudoers.d/oko-admin \
    ${sysconfdir}/ssh/sshd_config.d/20-oko-password.conf \
"

RDEPENDS:${PN} = "sudo openssh-sshd"

