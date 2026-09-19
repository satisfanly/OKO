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

SUMMARY = "AMD System Management Interface"
DESCRIPTION = "AMD SMI library and amd-smi command line utility"
HOMEPAGE = "https://github.com/ROCm/amdsmi"

LICENSE = "MIT AND NCSA"

LIC_FILES_CHKSUM = " \
    file://LICENSE;md5=011efd641e78f59c30a3aceda5493c11 \
    file://${UNPACKDIR}/esmi/License.txt;md5=987c23429df647db690af024cb58f44a \
"

SRC_URI = " \
    git://github.com/ROCm/amdsmi.git;protocol=https;nobranch=1;name=amdsmi;destsuffix=amdsmi \
    git://github.com/amd/esmi_ib_library.git;protocol=https;nobranch=1;name=esmi;destsuffix=esmi \
"

# AMD SMI 26.2.2
SRCREV_amdsmi = "1e91f3c1527617066f50c22f9ec4368fe82e1a3c"

# ESMI esmi_pkg_ver-4.2
SRCREV_esmi = "fae8e8e7af086bcf974d406bbac0c6e1ed1a25f9"

SRCREV_FORMAT = "amdsmi_esmi"

S = "${UNPACKDIR}/amdsmi"

inherit cmake pkgconfig

DEPENDS = " \
    libdrm \
"

#
# Yocto-specific source fixes.
#
# 1. AMD-SMI upstream tries to clone/update esmi_ib_library itself from
#    CMake. BitBake must own all source fetching, so remove that logic.
#
# 2. AMD-SMI 26.2.2 contains its own drm_color_ctm_3x4 definition in
#    include/amd_smi/impl/amdgpu_drm.h. Newer libdrm already defines it
#    in drm_mode.h, causing a C++ redefinition error.
#

python do_patch:append() {
    import os
    import re

    s = d.getVar("S")

    #
    # ---------------------------------------------------------------
    # Disable AMD-SMI configure-time ESMI git clone/update
    # ---------------------------------------------------------------
    #
    cmake_file = os.path.join(s, "CMakeLists.txt")

    with open(cmake_file, "r", encoding="utf-8") as f:
        text = f.read()

    replacement_marker = "Using BitBake supplied esmi_ib_library"

    if replacement_marker not in text:
        #
        # Match the complete upstream source-fetching section.
        # Keep the subsequent amd_hsmp.h copy and ESMI configuration.
        #
        pattern = re.compile(
            r'if\(ENABLE_ESMI_LIB\)\s*'
            r'# Supported esmi library version tag.*?'
            r'(?=\s*# Make sure to update the amd_hsmp\.h file)',
            re.DOTALL
        )

        replacement = """if(ENABLE_ESMI_LIB)
    if(NOT EXISTS "${PROJECT_SOURCE_DIR}/esmi_ib_library/src")
        message(FATAL_ERROR "esmi_ib_library was not supplied by BitBake")
    endif()
    message(STATUS "Using BitBake supplied esmi_ib_library")
"""

        text_new, count = pattern.subn(
            replacement,
            text,
            count=1
        )

        if count != 1:
            bb.fatal(
                "amd-smi: unable to locate upstream "
                "esmi_ib_library fetch block"
            )

        with open(cmake_file, "w", encoding="utf-8") as f:
            f.write(text_new)

    #
    # ---------------------------------------------------------------
    # Remove duplicate drm_color_ctm_3x4
    # ---------------------------------------------------------------
    #
    amdgpu_drm = os.path.join(
        s,
        "include",
        "amd_smi",
        "impl",
        "amdgpu_drm.h"
    )

    with open(amdgpu_drm, "r", encoding="utf-8") as f:
        text = f.read()

    drm_replacement_marker = (
        "drm_color_ctm_3x4 is provided by libdrm/drm_mode.h"
    )

    if drm_replacement_marker not in text:
        pattern = re.compile(
            r'/\* FIXME wrong namespace! \*/\s*'
            r'struct drm_color_ctm_3x4\s*\{.*?\n\};',
            re.DOTALL
        )

        text_new, count = pattern.subn(
            "/* drm_color_ctm_3x4 is provided by libdrm/drm_mode.h */",
            text,
            count=1
        )

        if count != 1:
            bb.fatal(
                "amd-smi: unable to locate bundled "
                "drm_color_ctm_3x4 definition"
            )

        with open(amdgpu_drm, "w", encoding="utf-8") as f:
            f.write(text_new)
}

#
# Put BitBake-fetched ESMI where AMD-SMI expects to find it.
#
do_configure:prepend() {
    rm -rf ${S}/esmi_ib_library

    mkdir -p ${S}/esmi_ib_library
    cp -a ${UNPACKDIR}/esmi/. ${S}/esmi_ib_library/
}

do_install:append() {
    #
    # Upstream copies libamd_smi.so into the Python package to make
    # it self-contained:
    #
    #   /usr/share/amd_smi/amdsmi/libamd_smi.so
    #
    # We already install the real shared library into ${libdir}.
    # Keeping both makes Yocto register two providers for the same
    # SONAME (libamd_smi.so.26).
    #
    # AMD-SMI's wrapper already searches /usr/lib/libamd_smi.so.26,
    # so the bundled duplicate is unnecessary.
    #
    rm -f ${D}${datadir}/amd_smi/amdsmi/libamd_smi.so
}

EXTRA_OECMAKE += " \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_CLI=ON \
    -DBUILD_WRAPPER=OFF \
    -DBUILD_RUST_WRAPPER=OFF \
    -DENABLE_ESMI_LIB=ON \
    -DENABLE_LDCONFIG=OFF \
"

#
# amd-smi is a Python CLI.
#
# argcomplete is optional upstream; amd-smi handles its absence.
#
RDEPENDS:${PN} += " \
    python3-core \
    python3-modules \
"

#
# Runtime files.
#
# Upstream installs:
#
#   /usr/bin/amd-smi
#   /usr/libexec/amdsmi_cli/
#   /usr/share/amd_smi/amdsmi/
#   /usr/lib/libamd_smi.so.*
#
FILES:${PN} += " \
    ${bindir}/amd-smi \
    ${libexecdir}/amdsmi_cli \
    ${datadir}/amd_smi \
    ${libdir}/libamd_smi.so.* \
"

#
# Development files.
#
FILES:${PN}-dev += " \
    ${includedir} \
    ${libdir}/libamd_smi.so \
    ${libdir}/cmake/amd_smi \
    ${datadir}/doc/amd-smi-lib \
"
