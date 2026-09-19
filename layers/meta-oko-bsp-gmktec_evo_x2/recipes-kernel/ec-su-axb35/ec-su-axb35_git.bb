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

SUMMARY = "Sixunited AXB35-02 embedded controller driver"
DESCRIPTION = "Linux EC driver providing fan, temperature and APU power-mode control for Sixunited AXB35-02 boards, including GMKtec EVO-X2"
HOMEPAGE = "https://github.com/cmetz/ec-su_axb35-linux"

LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRC_URI = "git://github.com/cmetz/ec-su_axb35-linux.git;protocol=https;branch=main"
SRCREV = "${AUTOREV}"

PV = "0.1+git"

inherit module

COMPATIBLE_HOST = "x86_64.*-linux"

# Upstream calls:
#   $(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules
#
# Yocto's module.bbclass already supplies O=, CC, LD, AR, etc.
EXTRA_OEMAKE += "KERNEL_BUILD=${STAGING_KERNEL_DIR}"

KERNEL_MODULE_AUTOLOAD += "ec_su_axb35"
