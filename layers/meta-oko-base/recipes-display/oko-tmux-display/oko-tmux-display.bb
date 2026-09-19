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

SUMMARY = "OKO shared display for local interfaces like serial and VT"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://oko-tmux-app-launcher \
    file://oko-tmux-app.service \
    file://oko-tmux-display-serial.conf \
    file://oko-tmux-display.tmux.conf \
    file://oko-tmux-display@.service \
    file://oko-tmux-display-serial@.service \
"
S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_PACKAGES = "${PN}"
SYSTEMD_SERVICE:${PN} = " \
    oko-tmux-app.service \
    oko-tmux-display@tty1.service \
    oko-tmux-display@ttyS0.service \
"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${libexecdir}
    install -m 0755 ${S}/oko-tmux-app-launcher \
        ${D}${libexecdir}/

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/oko-tmux-app.service \
        ${D}${systemd_system_unitdir}/

    install -m 0644 ${S}/oko-tmux-display@.service \
        ${D}${systemd_system_unitdir}/oko-tmux-display@tty1.service

    install -m 0644 ${S}/oko-tmux-display-serial@.service \
        ${D}${systemd_system_unitdir}/oko-tmux-display@ttyS0.service
    install -d \
        ${D}${systemd_system_unitdir}/oko-tmux-display@ttyS0.service.d
    install -m 0644 ${S}/oko-tmux-display-serial.conf \
        ${D}${systemd_system_unitdir}/oko-tmux-display@ttyS0.service.d/terminal.conf
                                                                                  
    install -d ${D}${sysconfdir}
    install -m 0644 ${S}/oko-tmux-display.tmux.conf \
        ${D}${sysconfdir}/
}

# Other files automatically added by inherited systemd class
FILES:${PN} += " \
    ${systemd_system_unitdir}/oko-tmux-display@ttyS0.service.d/* \
"

RDEPENDS:${PN} = " \
    oko-base \
    oko-locale \
    coreutils \
    bash \
    tmux \
"

