#!/usr/bin/env python3
"""Let the kernel relocate ADRP relocations (fixes insmod -ENOEXEC on this SoC).

Usage:
    python3 port/patch_adrp_reloc.py <kernel_src_dir>

Why this exists
---------------
The Archytas kernel is built with CONFIG_ARM64_ERRATUM_843419=y, which makes
arch/arm64/kernel/module.c compile the R_AARCH64_ADR_PREL_PG_HI21(_NC) handlers
out:

    #ifndef CONFIG_ARM64_ERRATUM_843419
        case R_AARCH64_ADR_PREL_PG_HI21_NC:
        case R_AARCH64_ADR_PREL_PG_HI21: ...
    #endif

so any module containing an ADRP relocation is refused with

    module wmt_drv: unsupported RELA relocation: 275
    insmod: failed to load ...: Exec format error

Upstream's assumption is that modules are built with -mcmodel=large and hence
contain no ADRP at all. That holds -- `-mcmodel=large` really is on the compile
line (read it back from the DW_AT_producer string baked into the shipped .ko) --
but GCC 11 still emits ADRP for the per-function literal pools it places inside
.text (`$d` mapping symbols). All 161 ADRPs in bt_drv.ko target the .text
section symbol itself, with small 8-byte-aligned addends sitting immediately
after a function's last instruction. No flag removes them.

So drop the guard and let the ADRP handlers run. Deliberately NOT done instead:
disabling CONFIG_ARM64_ERRATUM_843419 in the defconfig -- that would also drop
--fix-cortex-a53-843419 from LDFLAGS_vmlinux and remove the erratum workaround
from the kernel itself (real Cortex-A53 hardware bug: "a load or store might
access an incorrect address"). This patch keeps the kernel's own workaround and
only relaxes the module-loader policy.

IMPORTANT: this changes the KERNEL, so it must be applied BEFORE the kernel is
compiled (see .github/workflows/build.yml), and the resulting boot image has to
be reflashed once. It is idempotent -- safe to run on every build.
"""
import re
import sys
from pathlib import Path

MARKER = "ARCHYTAS_ADRP_RELOC"

GUARD = re.compile(
    r"[ \t]*#ifndef CONFIG_ARM64_ERRATUM_843419\n"
    r"([ \t]*case R_AARCH64_ADR_PREL_PG_HI21_NC:\n"
    r"[\s\S]*?break;\n)"
    r"[ \t]*#endif\n")

NOTE = (
    "/* " + MARKER + ": the guard `#ifndef CONFIG_ARM64_ERRATUM_843419` used to\n"
    " * compile these ADRP handlers out, which made every module containing an\n"
    " * ADRP relocation unloadable (`unsupported RELA relocation: 275`, -ENOEXEC).\n"
    " * These MTK connectivity modules DO contain them even though they are built\n"
    " * with -mcmodel=large, so relocate them normally.\n"
    " * See port/patch_adrp_reloc.py for the full rationale. */\n")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.split("Usage:")[1].split("\n")[0].strip()
                 if "Usage:" in __doc__ else "usage: patch_adrp_reloc.py <kernel_src_dir>")
    target = Path(sys.argv[1]) / "arch" / "arm64" / "kernel" / "module.c"
    if not target.is_file():
        sys.exit("ERROR: not found: %s" % target)

    src = target.read_text(encoding="utf-8", errors="surrogateescape")
    if MARKER in src:
        print("   already patched: %s" % target)
        return 0

    m = GUARD.search(src)
    if not m:
        sys.exit("ERROR: could not find the CONFIG_ARM64_ERRATUM_843419 ADRP guard "
                 "in %s -- the kernel source layout changed" % target)

    body = m.group(1)
    out = src[:m.start()] + NOTE + body + src[m.end():]
    target.write_text(out, encoding="utf-8", errors="surrogateescape")
    n = body.count("case R_AARCH64_ADR_PREL_PG_HI21")
    print("   patched %s: %d ADRP case label(s) now compiled unconditionally"
          % (target, n))

    if "#ifndef CONFIG_ARM64_ERRATUM_843419" in out:
        # Anchor per LINE. The explanatory comment written above deliberately
        # quotes the guard text, so a plain substring search always matched it
        # and made the patcher report failure after a successful patch.
        stray = [ln for ln in out.splitlines()
                 if ln.strip().startswith("#ifndef CONFIG_ARM64_ERRATUM_843419")]
        if stray:
            sys.exit("ERROR: %d '#ifndef CONFIG_ARM64_ERRATUM_843419' line(s) "
                     "survived; refusing to continue (the ADRP handlers may still "
                     "be compiled out)" % len(stray))
    return 0


if __name__ == "__main__":
    sys.exit(main())
