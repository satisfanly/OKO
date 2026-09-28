SUMMARY = "Olla LLM proxy and load balancer"
DESCRIPTION = "Lightweight proxy and load balancer for Ollama and other LLM inference servers"
HOMEPAGE = "https://github.com/thushan/olla"

LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

GO_IMPORT = "github.com/thushan/olla"

SRC_URI = " \
    git://github.com/thushan/olla.git;protocol=https;branch=main;destsuffix=${GO_SRCURI_DESTSUFFIX} \
    file://config.yaml \
    file://olla.service \
"

SRCREV = "9fed553fc1ccaba96552d9e23aaf32c934471f21"

PV = "0.0.0+git${SRCPV}"

inherit go-mod systemd

# Olla is pure Go. Upstream release builds also use CGO_ENABLED=0.
export CGO_ENABLED = "0"

#
# Olla has external Go modules and does not vendor them in-tree.
#
# This allows Go to fetch them during do_compile.
# Later, if you want a fully reproducible/offline Yocto recipe,
# we can convert this to go-vendor.
#
do_compile[network] = "1"

do_compile() {
    cd ${B}/src/${GO_IMPORT}

    ${GO} build \
        ${GOBUILDFLAGS} \
        -mod=mod \
        -trimpath \
        -o ${B}/olla \
        .
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${B}/olla ${D}${bindir}/olla

    install -d ${D}${sysconfdir}/olla
    install -m 0644 ${UNPACKDIR}/config.yaml \
        ${D}${sysconfdir}/olla/config.yaml

    install -d ${D}${systemd_system_unitdir}

    install -m 0644 ${UNPACKDIR}/olla.service \
        ${D}${systemd_system_unitdir}/olla.service
}

SYSTEMD_SERVICE:${PN} = "olla.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

FILES:${PN} += " \
    ${bindir}/olla \
    ${sysconfdir}/olla/config.yaml \
    ${systemd_system_unitdir}/olla.service \
"
