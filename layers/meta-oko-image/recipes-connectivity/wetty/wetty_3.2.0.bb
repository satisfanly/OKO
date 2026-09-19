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

SUMMARY = "Wetty browser terminal"
DESCRIPTION = "Web terminal that opens a local SSH session without embedding credentials"
HOMEPAGE = "https://github.com/butlerx/wetty"
LICENSE = "MIT & MIT-0 & Apache-2.0 & BSD-3-Clause & ISC & CC-BY-4.0"
LIC_FILES_CHKSUM = " \
    file://LICENSE;md5=bc3215b79e96267aa77bb7426fe0332a \
    file://THIRD_PARTY_LICENSES.md;md5=1aa37d3863683d5a942be3db6f28059c \
"

# Yocto 6.0 intentionally disables the npm/npmsw fetchers. The checked-in
# archive contains an exact production dependency tree, its package lock and
# license manifest, with all prebuilt native addons removed.
SRC_URI = " \
    file://wetty-vendor-${PV}.tar.zst \
    file://wetty.in \
"

S = "${UNPACKDIR}/wetty-${PV}"

inherit python3native

DEPENDS = "nodejs-native"
RDEPENDS:${PN} = "nodejs openssh-ssh"

NPM_NODEDIR = "${RECIPE_SYSROOT_NATIVE}${prefix_native}"

def wetty_node_arch(arch):
    import re
    if re.match(r"i.86$", arch):
        return "ia32"
    if re.match(r"x86_64$", arch):
        return "x64"
    if re.match(r"aarch64$", arch):
        return "arm64"
    if re.match(r"(powerpc64|powerpc64le|ppc64le)$", arch):
        return "ppc64"
    if re.match(r"powerpc$", arch):
        return "ppc"
    return arch

NPM_ARCH = "${@wetty_node_arch(d.getVar('TARGET_ARCH'))}"

do_compile() {
    # node-pty is the only native module. npm is used solely as the local
    # lifecycle runner; the complete JS dependency tree is already unpacked.
    export npm_config_arch="${NPM_ARCH}"
    export npm_config_target_arch="${NPM_ARCH}"
    export npm_config_nodedir="${NPM_NODEDIR}"
    export npm_config_python="${PYTHON}"
    export npm_config_build_from_source="true"
    export npm_config_release="true"
    export npm_config_offline="true"
    export npm_config_proxy="http://invalid"
    export npm_config_https_proxy="http://invalid"
    export npm_config_cache="${WORKDIR}/npm-cache"
    export npm_config_audit="false"
    export npm_config_fund="false"
    export NODE_GYP_FORCE_PYTHON="${PYTHON}"

    cd ${S}
    npm rebuild node-pty --offline --no-audit --no-fund
    test -s ${S}/node_modules/node-pty/build/Release/pty.node
}

do_install() {
    install -d ${D}${libdir}/wetty
    cp --recursive --no-preserve=ownership ${S}/. ${D}${libdir}/wetty/

    # Keep the final target addon, not node-gyp's objects, generated makefiles
    # and intermediate libraries. node-gyp creates node-addon-api beside the
    # build directory and its *.target.mk files contain absolute build paths.
    # Neither directory is needed when loading the completed pty.node addon.
    install -m 0755 ${S}/node_modules/node-pty/build/Release/pty.node \
        ${WORKDIR}/pty.node
    rm -rf \
        ${D}${libdir}/wetty/node_modules/node-pty/build \
        ${D}${libdir}/wetty/node_modules/node-pty/node-addon-api
    install -d ${D}${libdir}/wetty/node_modules/node-pty/build/Release
    install -m 0755 ${WORKDIR}/pty.node \
        ${D}${libdir}/wetty/node_modules/node-pty/build/Release/pty.node

    install -d ${D}${bindir}
    sed -e 's|@LIBDIR@|${libdir}|g' ${UNPACKDIR}/wetty.in \
        > ${D}${bindir}/wetty
    chmod 0755 ${D}${bindir}/wetty
}

FILES:${PN} += "${libdir}/wetty"

# The executable addon is target code and must not inherit the host's ABI.
PACKAGE_ARCH = "${TUNE_PKGARCH}"
