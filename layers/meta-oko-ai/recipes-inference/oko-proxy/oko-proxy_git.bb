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

SUMMARY = "OKO proxies collection"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    https://github.com/satisfanly/oko-proxy \
    file://oko-ai-proxy.service \
    file://oko-ai-proxy-stream.service \
    file://oko-ai-proxy-openai.service \
"
SRCREV = "4973f6e0ad4c49b93232abb028f3238d3013433f"
PV = "1.0+git"

inherit python3native systemd

SYSTEMD_PACKAGES = "${PN}"
SYSTEMD_SERVICE:${PN} = " \
    oko-ai-proxy-openai.service \
    oko-ai-proxy-stream.service \
    oko-ai-proxy.service \
"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} += " \
    python3-core \
    python3-aiohttp \
    python3-pyyaml \
    python3-httpx \
    python3-fastapi \
"

do_install() {
    install -d ${D}${bindir}
    install -d ${D}${systemd_system_unitdir}

    install -m 0755 ${S}/ollama_proxy_llama.py \
        ${D}${bindir}/oko-ai-proxy

    install -m 0755 ${S}/ollama_proxy.py \
        ${D}${bindir}/oko-extern-chain-simple-noloop

    install -m 0755 ${S}/ollama_proxy_stream.py \
        ${D}${bindir}/oko-ai-proxy-stream

    install -m 0755 ${S}/openai_ollama_proxy.py \
        ${D}${bindir}/oko-ai-proxy-openai

    install -m 0755 ${S}/ollama_sniffer.py \
        ${D}${bindir}/oko-ai-sniffer

    install -m 0644 ${UNPACKDIR}/oko-ai-proxy-openai.service \
        ${D}${systemd_system_unitdir}/

    install -m 0644 ${UNPACKDIR}/oko-ai-proxy-stream.service \
        ${D}${systemd_system_unitdir}/

    install -m 0644 ${UNPACKDIR}/oko-ai-proxy.service \
        ${D}${systemd_system_unitdir}/
}
