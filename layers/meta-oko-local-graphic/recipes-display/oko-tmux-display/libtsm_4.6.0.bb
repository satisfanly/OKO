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

# Backported recipe from meta-openembedded  (dependency of kmscon)
# TODO: remove it once we will upgrade to upstream version
SUMMARY = "Terminal Emulator State Machine"
DESCRIPTION = "\
    TSM is a state machine for DEC VT100-VT520 compatible terminal emulators. \
    It tries to support all common standards while keeping compatibility to \
    existing emulators like xterm, gnome-terminal, konsole, etc. \
    TSM itself does not provide any rendering nor window management. It is a \
    simple plain state machine without any external dependencies. \
"
HOMEPAGE = "https://github.com/kmscon/libtsm"
BUGTRACKER = "https://github.com/kmscon/libtsm/issues"
CVE_PRODUCT = "libtsm"

SECTION = "libs"

LICENSE = "MIT & LGPL-2.1-or-later"
LIC_FILES_CHKSUM = "\
    file://COPYING;md5=69e8256cdc4e949f86fedf94b1b320b4 \
    file://LICENSE_htable;md5=2d5025d4aa3495befef8f17206a5b0a1 \
"

DEPENDS = "xkeyboard-config"

SRC_URI = "git://github.com/kmscon/libtsm;protocol=https;branch=main;tag=v${PV}"
SRCREV = "e1e4d296f0963d1641456f1f778f0ac090429a3e"

inherit meson pkgconfig

EXTRA_OEMESON:append = " \
    -Dextra_debug=false \
    -Dtests=false \
    -Dgtktsm=false \
"
