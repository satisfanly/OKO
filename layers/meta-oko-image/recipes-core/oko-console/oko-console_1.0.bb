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

SUMMARY = "OKO local dashboard and browser terminal services"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://oko-dashboard.py \
    file://oko-tmux-app \
    file://oko-web-terminal.service \
"

S = "${UNPACKDIR}"

inherit systemd

# Comment this to enable wetty
PACKAGECONFIG:remove = "wetty"

PACKAGECONFIG ??= "wetty"

#                       enable args
#                       | disable args
#                       | | build deps
#                       | | | runtime deps
#                       v v v v
PACKAGECONFIG[wetty] = ",,,wetty"

SYSTEMD_PACKAGES = "${PN}"

SYSTEMD_SERVICE:${PN} = " \
    ${@bb.utils.contains('PACKAGECONFIG', 'wetty', 'oko-web-terminal.service', '', d)} \
"

SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${S}/oko-dashboard.py ${D}${bindir}/oko-dashboard

    install -d ${D}${libexecdir}
    install -m 0755 ${S}/oko-tmux-app \
        ${D}${libexecdir}/

    if ${@bb.utils.contains('PACKAGECONFIG', 'wetty', 'true', 'false', d)}; then
        install -d ${D}${systemd_system_unitdir}
        install -m 0644 ${S}/oko-web-terminal.service \
            ${D}${systemd_system_unitdir}/
    fi
}

RDEPENDS:${PN} = " \
    packagegroup-oko-base \
    oko-tmux-display \
    python3-modules \
    python3-textual \
    python3-core \
    python3-qrcode \
    iproute2 \
    openssh-ssh \
    bash \
"
