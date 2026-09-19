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

SUMMARY = "Small Unicode character-category data library"
HOMEPAGE = "https://github.com/tsutsu3/uc.micro-py"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=2bdce4f0cca8b092dd7889cef7f7fe20"

PYPI_PACKAGE = "uc_micro_py"
SRC_URI[sha256sum] = "c53691e495c8db60e16ffc4861a35469b0ba0821fe409a8a7a0a71864d33a811"

inherit pypi python_setuptools_build_meta
