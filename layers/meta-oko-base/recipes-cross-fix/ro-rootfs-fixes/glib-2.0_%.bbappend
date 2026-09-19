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

#
# GLib itself does not install loadable GIO modules in the standard OKO
# configuration. However gio-module-cache.bbclass defaults
# GIO_MODULE_PACKAGES to ${PN}, which causes libglib-2.0-0 to schedule
# update_gio_module_cache during do_rootfs.
#
# That intercept executes the target gio-querymodules through qemuwrapper,
# which is unavailable because OKO intentionally opts out of qemu-usermode.
#
# Do not register the cache-update postinst for the core GLib package.
# Actual GIO module providers must inherit gio-module-cache themselves and
# are responsible for updating giomodule.cache.
#
GIO_MODULE_PACKAGES:class-target = ""

#
# gio-module-cache.bbclass adds qemuwrapper-cross unconditionally, even when
# GIO_MODULE_PACKAGES is empty. Since no GIO cache postinst is registered,
# qemuwrapper-cross is unused and would only pull qemu-native into the build.
#
PACKAGE_WRITE_DEPS:remove:class-target = "qemuwrapper-cross"

#
# Safety check: if a future GLib configuration starts installing actual
# GIO modules, fail the build instead of silently shipping them without
# cache generation.
#
do_install:append:class-target() {
    modules="${D}${libdir}/gio/modules"

    if [ -d "$modules" ] &&
       find "$modules" -maxdepth 1 \
           \( -type f -o -type l \) \
           -name '*.so*' \
           -print -quit | grep -q .; then
        bbfatal "glib-2.0 installs GIO modules, but GIO_MODULE_PACKAGES is disabled by OKO"
    fi
}
