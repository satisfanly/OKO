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

# This is actually configuration flow in numpy (not test):
# There are two possible approaches in numpy:
# 1. Run target probe
#    compile → execute → inspect result
#                 ↑
#              requires QEMU / runnable target
# 
# 2. Tell NumPy the known result
#    longdouble_format = 'INTEL_EXTENDED_16_BYTES_LE'
#                 ↑
#              no execution
#
# We takes #2
#
# NumPy otherwise tries to execute a target binary to detect the
# long-double representation. OKO intentionally has no qemu-usermode.
#
# x86_64 SysV ABI:
# sizeof(long double) == 16, x87 extended precision, little endian.
do_write_config:append() {
    sed -i "/^\[properties\]$/a longdouble_format = 'INTEL_EXTENDED_16_BYTES_LE'" \
        ${WORKDIR}/meson.cross
}
