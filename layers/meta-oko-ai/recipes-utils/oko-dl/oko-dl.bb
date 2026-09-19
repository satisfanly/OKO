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

SUMMARY = "OKO Hugging Face GGUF model downloader"
DESCRIPTION = "Downloads a selected GGUF plus available mmproj and MTP companion GGUFs from Hugging Face."
HOMEPAGE = "https://huggingface.co/"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://oko-dl.py"

S = "${UNPACKDIR}"

RDEPENDS:${PN} = " \
    python3-core \
    python3-json \
    python3-netclient \
    python3-io \
"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${S}/oko-dl.py ${D}${bindir}/oko-dl
}

FILES:${PN} = "${bindir}/oko-dl"

