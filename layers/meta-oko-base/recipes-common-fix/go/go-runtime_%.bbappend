# go-runtime_%.bbappend
#
# Work around Go/cgo embedding Yocto TMPDIR paths into runtime/cgo.a.
#
# Do a byte-for-byte same-length replacement of TMPDIR in the installed
# archive. Since the replacement has exactly the same length, neither the
# ar archive layout nor ELF member offsets/relocations change.

do_install:append() {
    found=0

    for archive in "${D}${libdir}/go/pkg/"*_dynlink/runtime/cgo.a; do
        [ -f "$archive" ] || continue
        found=1

        bbnote "Checking build paths in $archive"

        python3 - "$archive" "${TMPDIR}" <<'PY'
import os
import sys

archive = sys.argv[1]
tmpdir = os.fsencode(sys.argv[2])

with open(archive, "rb") as f:
    data = f.read()

count = data.count(tmpdir)

if count == 0:
    print(f"{archive}: no TMPDIR references")
    sys.exit(0)

#
# The replacement MUST have exactly the same number of bytes as TMPDIR.
# This allows us to patch the archive in place without changing any
# archive member size, ELF offset, relocation, or string-table offset.
#
prefix = b"/usr/src/debug/yocto-tmp"

if len(prefix) > len(tmpdir):
    raise SystemExit(
        f"replacement prefix ({len(prefix)}) is longer than "
        f"TMPDIR ({len(tmpdir)})"
    )

replacement = prefix + (b"_" * (len(tmpdir) - len(prefix)))

assert len(replacement) == len(tmpdir)

patched = data.replace(tmpdir, replacement)

if len(patched) != len(data):
    raise SystemExit("BUG: patched archive size changed")

if tmpdir in patched:
    raise SystemExit("TMPDIR still present after replacement")

tmp = archive + ".patched"

with open(tmp, "wb") as f:
    f.write(patched)
    f.flush()
    os.fsync(f.fileno())

os.replace(tmp, archive)

# Read it back from disk and verify.
with open(archive, "rb") as f:
    verify = f.read()

if tmpdir in verify:
    raise SystemExit("TMPDIR still present after writing archive")

if len(verify) != len(data):
    raise SystemExit("archive size changed after writing")

print(
    f"{archive}: replaced {count} TMPDIR occurrence(s), "
    f"size unchanged ({len(data)} bytes)"
)
PY

        #
        # Verify that we did not corrupt the ar container.
        #
        if ! ${AR} t "$archive" >/dev/null; then
            bbfatal "Patched cgo.a is no longer a valid ar archive: $archive"
        fi

        #
        # This is exactly what package QA was detecting.
        #
        if grep -aFq "${TMPDIR}" "$archive"; then
            bbfatal "TMPDIR STILL PRESENT in $archive after binary rewrite"
        fi

        bbnote "Verified sanitized cgo archive: $archive"
    done

    if [ "$found" != "1" ]; then
        bbfatal "Could not find *_dynlink/runtime/cgo.a under ${D}${libdir}/go/pkg"
    fi
}
