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

SUMMARY = "High-performance LLM inference in pure C/C++"
HOMEPAGE = "https://github.com/satisfanly/oko-llama.cpp"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=223b26b3c1143120c87e2b13111d3e99"

SRC_URI = "https://github.com/satisfanly/oko-llama.cpp"
SRCREV = "dc1534b8cfe2602afee6f289bea5d42c207f7b98"
PV = "1.0+git"

inherit cmake pkgconfig

#
# Optional backends/features.
#
PACKAGECONFIG ??= " \
    ${@bb.utils.filter('DISTRO_FEATURES', 'vulkan', d)} \
    openmp \
"

PACKAGECONFIG[openmp] = \
    "-DGGML_OPENMP=ON,-DGGML_OPENMP=OFF"

PACKAGECONFIG[vulkan] = \
    "-DGGML_VULKAN=ON,-DGGML_VULKAN=OFF,vulkan-loader vulkan-headers spirv-headers shaderc-native"

EXTRA_OECMAKE += " \
    -DLLAMA_BUILD_IS_DEV=OFF \
    \
    -DLLAMA_BUILD_COMMON=ON \
    -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_BUILD_SERVER=ON \
    \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_APP=OFF \
    \
    -DGGML_BUILD_TESTS=OFF \
    -DGGML_BUILD_EXAMPLES=OFF \
    \
    -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_USE_PREBUILT_UI=OFF \
    \
    -DLLAMA_OPENSSL=OFF \
    \
    -DGGML_NATIVE=OFF \
    -DGGML_CCACHE=OFF \
    \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_STATIC=ON \
    \
    -DHOST_CXX_COMPILER=${OECMAKE_NATIVE_CXX_COMPILER} \
    -DGGML_VULKAN_SHADERS_GEN_TOOLCHAIN=${WORKDIR}/toolchain-native.cmake \
"

#
# Critical:
# Don't build the default CMake "all" target.
#
OECMAKE_TARGET_COMPILE = "llama-server"

#
# Don't invoke upstream's complete `cmake --install`, because that install
# graph contains llama/ggml development artifacts that we don't currently
# want to package.
#
do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${B}/bin/llama-server \
        ${D}${bindir}/llama-server
}

FILES:${PN} = "${bindir}/llama-server"
