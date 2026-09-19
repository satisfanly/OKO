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

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

#
# fontcache.bbclass normally adds a do_rootfs postinstall intercept which
# executes target fc-cache through qemuwrapper.
#
# OKO intentionally disables qemu-usermode, so don't register that
# build-time intercept. Fontconfig caches are architecture-dependent and
# therefore cannot safely be generated using fontconfig-native for an
# arbitrary target architecture.
#
FONT_PACKAGES:class-target = ""

#
# fontcache.bbclass adds qemuwrapper-cross to PACKAGE_WRITE_DEPS
# unconditionally, even when FONT_PACKAGES is empty. Since the font cache
# postinst is disabled, the wrapper is unused and would only pull
# qemu-native into the build.
#
PACKAGE_WRITE_DEPS:remove:class-target = "qemuwrapper-cross"

SRC_URI:append:class-target = " file://fontconfig-cache.service"

inherit systemd

SYSTEMD_PACKAGES:class-target = "${PN}-common"
SYSTEMD_SERVICE:${PN}-common = "fontconfig-cache.service"
SYSTEMD_AUTO_ENABLE:${PN}-common = "enable"

FILES:${PN}-common:append = " ${systemd_system_unitdir}/fontconfig-cache.service"

# FONT_PACKAGES="" prevents fontcache.bbclass from adding its normal
# fontconfig-utils dependency, so retain it explicitly.
RDEPENDS:${PN}-common:append = " fontconfig-utils"

do_install:append:class-target() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 \
        ${UNPACKDIR}/fontconfig-cache.service \
        ${D}${systemd_system_unitdir}/fontconfig-cache.service
}
