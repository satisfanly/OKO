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

SUMMARY = "Unicode-aware link recognition library"
HOMEPAGE = "https://github.com/tsutsu3/linkify-it-py"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=a7aa82f01e7197249f7f2dcb0c0bac97"

PYPI_PACKAGE = "linkify_it_py"
SRC_URI[sha256sum] = "a78f40fee177eb912e9d2375074108378523c38d3fde5d3ee804f465b6cfbfee"

inherit pypi python_setuptools_build_meta

RDEPENDS:${PN} += "python3-uc-micro-py"
