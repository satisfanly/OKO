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

SUMMARY = "RyzenAdj power management utility"
DESCRIPTION = "Utility for adjusting AMD Ryzen APU power management settings through the SMU"
HOMEPAGE = "https://github.com/FlyGoat/RyzenAdj"

LICENSE = "LGPL-3.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/LGPL-3.0-only;md5=bfccfe952269fff2b407dd11f2f3083b"

SRC_URI = "git://github.com/FlyGoat/RyzenAdj.git;protocol=https;branch=master"
SRCREV = "${AUTOREV}"

PV = "0.19.0+git${SRCPV}"

DEPENDS = "pciutils"

inherit cmake pkgconfig

COMPATIBLE_HOST = "x86_64.*-linux"

EXTRA_OECMAKE = "\
    -DCMAKE_BUILD_TYPE=Release \
"

RDEPENDS:${PN} += " \
    ryzen-smu \
    bash \
    util-linux \
"

do_install:append() {
    # Preserve the actual RyzenAdj executable outside PATH.
    install -d ${D}${libexecdir}
    mv ${D}${bindir}/ryzenadj \
       ${D}${libexecdir}/ryzenadj.real

    # Replace /usr/bin/ryzenadj with a globally serialized wrapper.
    cat > ${D}${bindir}/ryzenadj <<EOF
#!/bin/bash

LOCK_FILE="/var/run/ryzenadj-global.lock"
REAL_RYZENADJ="${libexecdir}/ryzenadj.real"

exec ${bindir}/flock \
    --exclusive \
    --no-fork \
    "\${LOCK_FILE}" \
    "\${REAL_RYZENADJ}" "\$@"
EOF

    chmod 0755 ${D}${bindir}/ryzenadj
}
