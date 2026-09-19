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

RDEPENDS:${PN}:append = " \
    kmscon \
    ttf-dejavu-sans-mono \
"
# Install libdrm-tests to better debug possiblities
RDEPENDS:${PN}:append = " \
    libdrm-tests \
"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = " \
    file://oko-tmux-display-kmscon@.service \
"

do_install:append() {
    # 1) Remove existed (installed by base recipe) tty1 service
    # 2) Install service that runnig kmscon
    rm ${D}${systemd_system_unitdir}/oko-tmux-display@tty1.service
    install -m 0644 ${S}/oko-tmux-display-kmscon@.service \
        ${D}${systemd_system_unitdir}/oko-tmux-display@tty1.service
}
