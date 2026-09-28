SUMMARY = "LLM Router - transparent multi-provider LLM gateway"
DESCRIPTION = "Transparent Ollama/OpenAI compatible LLM router"
HOMEPAGE = "https://github.com/paularlott/llmrouter"

LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

GO_IMPORT = "github.com/paularlott/llmrouter"

SRC_URI = " \
    git://github.com/paularlott/llmrouter.git;protocol=https;branch=main;destsuffix=${GO_SRCURI_DESTSUFFIX} \
    file://config.toml \
    file://llmrouter.service \
"

SRCREV = "${AUTOREV}"
PV = "0.0.0+git${SRCPV}"

inherit go-mod systemd

export CGO_ENABLED = "0"

do_compile[network] = "1"

do_compile() {
    cd "${B}/src/${GO_IMPORT}"

    # Stub embedded UI; avoids nodejs-native/npm entirely.
    rm -rf web/dist
    install -d web/dist

    cat > web/dist/index.html <<'EOF'
<!doctype html>
<html>
<head><meta charset="utf-8"><title>LLM Router</title></head>
<body>Web UI disabled in this build.</body>
</html>
EOF

    ${GO} build \
        ${GOBUILDFLAGS} \
        -tags=server \
        -trimpath \
        -o "${B}/llmrouter" \
        .
}

do_install() {
    install -d "${D}${bindir}"
    install -m 0755 "${B}/llmrouter" \
        "${D}${bindir}/llmrouter"

    install -d "${D}${sysconfdir}/llmrouter"
    install -m 0644 "${UNPACKDIR}/config.toml" \
        "${D}${sysconfdir}/llmrouter/config.toml"

    install -d "${D}${systemd_system_unitdir}"
    install -m 0644 "${UNPACKDIR}/llmrouter.service" \
        "${D}${systemd_system_unitdir}/llmrouter.service"
}

SYSTEMD_SERVICE:${PN} = "llmrouter.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

FILES:${PN} += " \
    ${bindir}/llmrouter \
    ${sysconfdir}/llmrouter/config.toml \
    ${systemd_system_unitdir}/llmrouter.service \
"
