#!/usr/bin/env python3
"""Verify port/build_connectivity_modules.sh as it sits on disk.

Dev-time tool (needs the local kernel source copy and the downloaded CI
artifact, both of which only exist in the author's workspace). It checks:

  1. `bash -n` accepts the script.
  2. every embedded `python3 - ... <<'PY' ... PY` heredoc is valid Python.
  3. the step-2e kernel patch really removes the CONFIG_ARM64_ERRATUM_843419
     guard (and is idempotent), by running it against real kernel source.
  4. the `.modinfo` extractor really reads name=/vermagic= out of a real .ko.

The Bash tool rewrites backslashes inside heredocs, so running these snippets
straight from a shell command line is unreliable -- hence this harness. It pulls
each snippet verbatim out of the script, which is exactly what CI executes.
"""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "port" / "build_connectivity_modules.sh"
SRC = ROOT / "archimedes-kernel-64-main" / "arch" / "arm64" / "kernel" / "module.c"
ART = ROOT / "archytas-wifi-boot-and-modules" / "kernel" / "out-archytas" / "connectivity-modules"
EXE = sys.executable

text = SCRIPT.read_text(encoding="utf-8")
failures = []


def check(name, ok, detail=""):
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                            (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


print("== 1. bash -n ==")
r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
check("bash syntax OK", r.returncode == 0, r.stderr.strip())

print("\n== 2. embedded python heredocs parse ==")
blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
print("   found %d heredoc block(s)" % len(blocks))
for i, b in enumerate(blocks):
    try:
        ast.parse(b)
        check("block #%d parses" % i, True, "%d lines" % (b.count("\n") + 1))
    except SyntaxError as e:
        check("block #%d parses" % i, False, str(e))

print("\n== 3. step-2e kernel patch (real source) ==")
m = re.search(r"python3 - \"\$MODULE_C\" <<'PY'\n(.*?)\nPY\n", text, re.S)
if not m or not SRC.exists():
    print("   SKIP (patcher not found, or no local kernel source at %s)" % SRC)
else:
    patch_code = m.group(1)
    tmp = Path(tempfile.mkdtemp())
    target = tmp / "module.c"
    shutil.copy2(SRC, target)
    before = SRC.read_text(encoding="utf-8", errors="surrogateescape")
    rc1 = subprocess.run([EXE, "-c", patch_code, str(target)],
                         capture_output=True, text=True)
    rc2 = subprocess.run([EXE, "-c", patch_code, str(target)],
                         capture_output=True, text=True)
    after = target.read_text(encoding="utf-8", errors="surrogateescape")
    check("first apply exits 0", rc1.returncode == 0, rc1.stdout.strip() + rc1.stderr.strip())
    check("second apply is a no-op", rc2.returncode == 0 and "already patched" in rc2.stdout,
          rc2.stdout.strip())
    check("marker ARCHYTAS_ADRP_RELOC written", "ARCHYTAS_ADRP_RELOC" in after)
    check("guard #ifndef gone", "\n#ifndef CONFIG_ARM64_ERRATUM_843419" not in after)
    check("exactly one #endif removed",
          after.count("#endif") == before.count("#endif") - 1,
          "%d -> %d" % (before.count("#endif"), after.count("#endif")))
    check("ADRP cases now unconditional",
          "case R_AARCH64_ADR_PREL_PG_HI21:" in after
          and "case R_AARCH64_ADR_PREL_PG_HI21_NC:" in after)
    check("braces stay balanced", after.count("{") == after.count("}"))
    check("elsewhere untouched",
          after.count("case R_AARCH64_") == before.count("case R_AARCH64_"))
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== 4. .modinfo extractor on real artifacts ==")
mm = re.search(r"python3 - \"\$m\" <<'PY'\n(.*?)\nPY\n", text, re.S)
kos = sorted(ART.glob("*.ko")) if ART.exists() else []
if not mm or not kos:
    print("   SKIP (extractor not found, or no artifacts at %s)" % ART)
else:
    code = mm.group(1)
    for ko in kos:
        r = subprocess.run([EXE, "-c", code, str(ko)], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        check("%s readable" % ko.name, r.returncode == 0 and "vermagic=" in out, out)

print("\n================ %s ================" % (
    "ALL CHECKS PASS" if not failures else "FAILURES: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
