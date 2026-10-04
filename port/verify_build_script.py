#!/usr/bin/env python3
"""Verify the Archytas build plumbing as it sits on disk.

Dev-time harness (some checks need the local kernel source copy and the
downloaded CI artifact; those skip cleanly when absent). Checks:

  1. `bash -n` accepts port/build_connectivity_modules.sh.
  2. no `| head` pipeline in the shell script -- it runs under
     `set -euo pipefail`, so SIGPIPE (141) from find/grep becomes a fatal
     assignment failure. This exact trap killed a CI run right after the first
     module, with no diagnostic at all.
  3. every embedded `python3 - ... <<'PY' ... PY` heredoc is valid Python.
  4. port/patch_adrp_reloc.py removes the CONFIG_ARM64_ERRATUM_843419 guard
     from real kernel source, and is idempotent.
  5. the workflow applies that patch BEFORE it builds the kernel -- otherwise
     the boot image ships with the unpatched module loader (silent: the build
     goes green and insmod still fails on the device).
  6. module build order + immediate collection. build_mod() wipes the module's
     whole build directory, and 'wlan' is an ancestor of 'wlan/adaptor' -- so
     building the ancestor last DELETES an already-linked module, and a
     collect-at-the-end sweep then cannot find it. That was run #37189514054:
     wmt_chrdev_wifi.ko linked fine at 08:47:34, gone by the 08:48:14 find.
  7. the `.modinfo` extractor reads name=/vermagic= out of a real .ko.
  8. the CONFIG_MTK_COMBO_CHIP quoting fix + the two IC-ops gates. Kconfig keeps
     its string values quoted and common/Makefile matches them with quoted
     patterns (`$(filter "CONSYS_%",...)`), so overriding the variable with a
     BARE `CONSYS_6761` on the make command line silently switched off
     common_main/platform/mt6761.o -- the module then kept the __weak
     mtk_wcn_get_consys_ic_ops stub and mtk_wmt_probe() panicked on a NULL
     wmt_consys_ic_ops. Includes a negative control on the pre-fix artifact.
  9. the wlan core's chip FAMILY selection. `MTK_COMBO_CHIP=MT6761` selects
     nothing: wlan/Makefile keys the chip layer off the family word (CONNAC), so
     chips/connac/connac.o was never compiled and `mtk_axi_ids[0].driver_data`
     stayed 0 -> mtk_axi_probe+0x2c dereferenced NULL and panicked. Also includes
     a negative control.
 10. the Wi-Fi DTB patch. The ROM DTB has no "memory-region" on wifi@18000000
     and its wifi-reserve-memory node is a plain no-map MTK region, so the Gen4M
     core's of_reserved_mem_device_init() returns -ENODEV and mtk_axi_probe()
     exits before mtk_wcn_wmt_wlan_reg() -- no panic, no wlan0, no other error.
     Asserts the patched node matches the vendor 4g_stock.dtb structurally while
     keeping the Wi-Fi board's own charger limits, that "no-map" is retained
     (it is what routes the node to rmem_dma_setup instead of rmem_cma_setup),
     that the patch is idempotent, and that the workflow packages the patched
     DTB. Includes negative controls on the unpatched DTB.
 11. end-to-end size rehearsal against the REAL 32 MiB template. The template is
     byte-exact (kernel region = gzip + the 4G DTB, zero slack), so the patched
     DTB must be *exactly* the 4G DTB's size -- 84094 B, note 84094 % 4 == 2. This
     group runs make_wifi_template.py the way the workflow does and asserts the
     output is exactly 32 MiB, then feeds a valid FDT one byte larger and asserts
     the guard rejects it. That is the failure mode of run #37195577635: the FDT
     emitter rebuilt the strings block (+123 B) and padded the blob (+2 B), so the
     template grew to 33554542 B and package_archimedes_boot.py died one step
     later with the misleading "template must be an exact 32 MiB Android boot
     image". Skipped when the agui checkout or the ROM DTB is absent.
 12. CI trigger hygiene. The push trigger carries a paths-ignore list so that
     docs / this harness / the offline DTB analysis tools do not burn an ~11 min
     runner, while no file the build reads may ever appear in it. Both halves are
     asserted, because a silently ignored build input would leave a stale boot
     image looking green. Also asserts the filter is attached to push only --
     workflow_dispatch (fast lane) and pull_request stay unfiltered.

The Bash tool rewrites backslashes inside heredocs, so running snippets
straight from a shell command line is unreliable -- hence this harness.
"""
import ast
import fnmatch
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "port" / "build_connectivity_modules.sh"
PATCHER = ROOT / "port" / "patch_adrp_reloc.py"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SRC = ROOT / "archimedes-kernel-64-main" / "arch" / "arm64" / "kernel" / "module.c"
ART = ROOT / "archytas-wifi-boot-and-modules" / "kernel" / "out-archytas" / "connectivity-modules"
EXE = sys.executable

# Optional: point the shell-script checks at a different copy, e.g. an older
# revision, to prove they actually fail on the bug they are meant to catch:
#   git show HEAD:port/build_connectivity_modules.sh > /tmp/old.sh
#   python3 port/verify_build_script.py /tmp/old.sh
if len(sys.argv) > 1:
    SCRIPT = Path(sys.argv[1])

failures = []


def check(name, ok, detail=""):
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                            (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


text = SCRIPT.read_text(encoding="utf-8")

print("== 1. bash -n ==")
r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
check("bash syntax OK", r.returncode == 0, r.stderr.strip())

print("\n== 2. no `| head` under set -euo pipefail ==")
opts = re.search(r"^set .*pipefail.*$", text, re.M)
check("script sets pipefail", bool(opts), opts.group(0) if opts else "not found")
offenders = []
for i, ln in enumerate(text.splitlines()):
    s = ln.strip()
    # A leading-# line is a comment (this file documents the trap verbatim), and
    # a pipeline followed by `|| ...` is deliberately guarded against failure --
    # that is how the pre-existing curl|head|sed SHA lookup survives.
    if s.startswith("#") or re.search(r"^#", s):
        continue
    if not re.search(r"\|\s*head\b", s):
        continue
    if "||" in s.split("| head", 1)[1]:
        continue
    offenders.append((i + 1, s))
check("no unguarded `| head` pipeline", not offenders,
      "; ".join("line %d: %s" % o for o in offenders))

print("\n== 3. embedded python heredocs parse ==")
blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
print("   found %d heredoc block(s)" % len(blocks))
for i, b in enumerate(blocks):
    try:
        ast.parse(b)
        check("block #%d parses" % i, True, "%d lines" % (b.count("\n") + 1))
    except SyntaxError as e:
        check("block #%d parses" % i, False, str(e))

print("\n== 4. patch_adrp_reloc.py against real kernel source ==")
if not SRC.exists():
    print("   SKIP (no local kernel source at %s)" % SRC)
else:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "arch" / "arm64" / "kernel").mkdir(parents=True)
    target = tmp / "arch" / "arm64" / "kernel" / "module.c"
    shutil.copy2(SRC, target)
    before = SRC.read_text(encoding="utf-8", errors="surrogateescape")
    r1 = subprocess.run([EXE, str(PATCHER), str(tmp)], capture_output=True, text=True)
    r2 = subprocess.run([EXE, str(PATCHER), str(tmp)], capture_output=True, text=True)
    after = target.read_text(encoding="utf-8", errors="surrogateescape")
    check("first run exits 0", r1.returncode == 0, (r1.stdout + r1.stderr).strip())
    check("second run is a no-op",
          r2.returncode == 0 and "already patched" in r2.stdout, r2.stdout.strip())
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
    bad = subprocess.run([EXE, str(PATCHER), str(tmp / "nope")],
                         capture_output=True, text=True)
    check("bad path exits non-zero", bad.returncode != 0, bad.stderr.strip()[:80])
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== 5. workflow: patch step precedes the kernel build ==")
try:
    import yaml
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    names = [s.get("name") or ("uses:%s" % s.get("uses")) for s in steps]
    for n in names:
        print("   %2d  %s" % (names.index(n), n))
    def idx(frag):
        return next((i for i, n in enumerate(names) if frag.lower() in n.lower()), None)
    i_patch, i_kern, i_mod = idx("Patch kernel"), idx("Build kernel"), idx("Build connectivity")
    check("patch step exists", i_patch is not None)
    check("kernel build step exists", i_kern is not None)
    check("patch runs BEFORE the kernel build",
          i_patch is not None and i_kern is not None and i_patch < i_kern,
          "patch=%s kernel=%s" % (i_patch, i_kern))
    check("module step runs AFTER the kernel build",
          i_kern is not None and i_mod is not None and i_kern < i_mod,
          "kernel=%s module=%s" % (i_kern, i_mod))
    check("boot-image packaging is gated on the fast lane",
          any("modules_only" in (s.get("if") or "") and "Package boot" in (s.get("name") or "")
              for s in steps))
except ImportError:
    print("   SKIP (pyyaml not installed)")

print("\n== 6. module build order + immediate collection ==")
# Logical lines: drop comments, join backslash continuations. The `build_mod
# wlan` invocation spans 5 physical lines, so physical-line scanning is wrong.
_logical = []
_buf = ""
for _ln in text.splitlines():
    _s = _ln.split("#", 1)[0] if _ln.lstrip().startswith("#") else _ln
    if not _buf and not _s.strip():
        continue
    if _buf and _s.lstrip().startswith("#"):
        continue
    _buf += _s
    if _buf.rstrip().endswith("\\"):
        _buf = _buf.rstrip()[:-1]
        continue
    _logical.append(_buf.strip())
    _buf = ""

_builds = [(i, l.split()[1]) for i, l in enumerate(_logical) if l.startswith("build_mod ")]
_collects = [(i, l.split()[1]) for i, l in enumerate(_logical) if l.startswith("collect_mod ")]
_order = [d for _, d in _builds]
print("   build_mod order : %s" % " -> ".join(_order))
print("   collect_mod     : %s" % " -> ".join(d for _, d in _collects))

check("all four modules are built",
      sorted(_order) == sorted(["common", "wlan", "wlan/adaptor", "bt"]),
      "got %s" % _order)

# (a) every build must be immediately followed by its own collect_mod, before
#     the next build starts. Otherwise a later ancestor wipe can still lose it.
_missing_collect = []
for k, (bi, bd) in enumerate(_builds):
    nxt = _builds[k + 1][0] if k + 1 < len(_builds) else len(_logical)
    own = [ci for ci, cd in _collects if cd == bd and bi < ci < nxt]
    if not own:
        _missing_collect.append(bd)
check("each module is collected right after it is built", not _missing_collect,
      "no collect_mod before the next build_mod for: %s" % _missing_collect)

# (b) an ancestor directory must be built before its descendants, because
#     build_mod() wipes the whole directory. 'wlan' must precede 'wlan/adaptor'.
_bad_pairs = []
for i, a in enumerate(_order):
    for j, b in enumerate(_order):
        if a != b and b.startswith(a + "/") and j < i:
            _bad_pairs.append("%s built after %s" % (a, b))
check("ancestor modules are built before their descendants", not _bad_pairs,
      "; ".join(_bad_pairs))

# (c) OUTDIR must be wiped before the first build, or a stale .ko from an
#     earlier local run would satisfy the "all four present" assertion.
_outdir_def = next((i for i, l in enumerate(_logical)
                    if l.startswith('OUTDIR="$KOUT/connectivity-modules"')), None)
_wipe = next((i for i, l in enumerate(_logical) if l.startswith('rm -rf "$OUTDIR"')), None)
_first_build = _builds[0][0] if _builds else None
check("OUTDIR is defined and cleaned before the first build",
      None not in (_outdir_def, _wipe, _first_build)
      and _outdir_def < _wipe < _first_build,
      "define=%s wipe=%s first build=%s" % (_outdir_def, _wipe, _first_build))

# (d) the artifact assertion must name all four, and the rename step must not be
#     able to mistake a previously renamed file for the freshly built module.
_want = re.search(r"for _want in ([^;]+); do", text)
check("artifact assertion covers all four modules",
      bool(_want) and set(_want.group(1).split()) >=
      {"wmt_drv", "wmt_chrdev_wifi", "bt_drv", "wlan_drv_gen4m"},
      _want.group(1).strip() if _want else "not found")
check("rename step skips an already-renamed wlan_drv_gen4m.ko",
      "case \"$_cand\" in *'/wlan_drv_gen4m.ko') continue" in text)

# (e) the pre-rename copy must be deleted, or the artifact glob ships the same
#     ~55 MiB wlan core twice.
_i_rmdup = next((i for i, l in enumerate(_logical) if l.startswith('rm -f "$_w"')), None)
check("pre-rename wlan core copy is removed (no 55 MiB duplicate)", _i_rmdup is not None)

# (f) ordering: relocation profile (which needs .debug_info for DW_AT_producer)
#     -> strip -g -> .modinfo dump. Profiling after the strip would lose the
#     codegen flags; verifying modinfo before the strip would not prove the
#     SHIPPED bytes are intact.
_i_prof = next((i for i, l in enumerate(_logical)
                if l.startswith('echo "=== relocation profile ==="')), None)
_i_strip = next((i for i, l in enumerate(_logical) if '"$STRIP" -g "$_m"' in l), None)
_i_modi = next((i for i, l in enumerate(_logical)
                if l.startswith('echo "=== modinfo')), None)
check("relocation profile is read before the strip", None not in (_i_prof, _i_strip)
      and _i_prof < _i_strip, "profile=%s strip=%s" % (_i_prof, _i_strip))
check(".modinfo is re-read AFTER the strip (verifies the shipped bytes)",
      None not in (_i_strip, _i_modi) and _i_strip < _i_modi,
      "strip=%s modinfo=%s" % (_i_strip, _i_modi))

print("\n== 7. .modinfo extractor on real artifacts ==")
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

print("\n== 8. COMBO_CHIP quoting fix + IC-ops linkage gates ==")
# (a) The regression itself. Kconfig stores string symbols WITH quotes
#     (auto.conf: CONFIG_MTK_COMBO_CHIP="CONSYS_6761") and common/Makefile:198
#     matches them with a quoted pattern, so passing a bare word on the make
#     command line makes every one of those filters miss. "$COMBO_CHIP_MAKE" is
#     the good form; a literal bare value is the bug.
_bare = [m for m in re.findall(r'CONFIG_MTK_COMBO_CHIP=(?!")([^\s\\"\']+)', text)
         if not m.startswith("$")]
check("CONFIG_MTK_COMBO_CHIP is never passed as a bare word", not _bare,
      "bare value(s): %s" % _bare)
check("the value is re-quoted before it is handed to make",
      'COMBO_CHIP_MAKE="\\"$COMBO_CHIP\\""' in text)
check("combo chip comes from the kernel's own config, not a literal",
      "kcfg_value() {" in text and "include/config/auto.conf" in text)
check("MTK_PLATFORM is derived with a non-empty fallback",
      'MTK_PLATFORM_VALUE=$(unq "$(kcfg_value CONFIG_MTK_PLATFORM' in text
      and "MTK_PLATFORM_VALUE=mt6761" in text)
check("MTK_PLATFORM is passed to the common module build",
      bool(re.search(r"build_mod common[\s\S]{0,160}?MTK_PLATFORM=\"\$MTK_PLATFORM_VALUE\"",
                     text)))

# (b) Gate #1 (chip file compiled) must sit between build_mod common and the
#     next module, so its failure is attributed to common's own build.
_i_bc = next((li for li, d in _builds if d == "common"), None)
_i_bw = next((li for li, d in _builds if d == "wlan"), None)
_i_g1 = next((i for i, l in enumerate(_logical)
              if "WMT IC-ops provider is in the link" in l), None)
check("mt6761.o presence gate runs after build_mod common and before wlan",
      None not in (_i_bc, _i_g1, _i_bw) and _i_bc < _i_g1 < _i_bw,
      "common@%s gate@%s wlan@%s" % (_i_bc, _i_g1, _i_bw))

# (c) Gate #2 must inspect the SHIPPED bytes, i.e. run after `strip -g`, and must
#     go through the symbol table (string counting cannot distinguish a
#     surviving __weak body from an overridden one).
_i_g2 = next((i for i, l in enumerate(_logical)
              if "IC-ops linkage gate" in l), None)
check("IC-ops symbol gate runs after the strip (gates the shipped .ko)",
      None not in (_i_g2, _i_strip) and _i_strip < _i_g2,
      "strip@%s gate@%s" % (_i_strip, _i_g2))
check("IC-ops gate goes through port/elf_symbols.py",
      _i_g2 is not None
      and "elf_symbols.py" in "\n".join(_logical[_i_g2 + 1: _i_g2 + 3]),
      "next lines: %s" % (_logical[_i_g2 + 1: _i_g2 + 3] if _i_g2 is not None else None))

# (d) The two regexes the gate relies on, tested against real symbol lines.
_rx = re.findall(r"grep -qE '(\^  (?:mtk_wcn_get_consys_ic_ops|consys_ic_ops)[^']+)'", text)
check("both gate regexes are present", len(_rx) == 2, "found %d" % len(_rx))
if len(_rx) == 2:
    rx_icops, rx_table = re.compile(_rx[0]), re.compile(_rx[1])
    STRONG = ("  mtk_wcn_get_consys_ic_ops                      "
              "GLOBAL FUNC    1     0x3f20       24")
    WEAKLINE = ("  mtk_wcn_get_consys_ic_ops                      "
                "WEAK   FUNC    1     0x71a0       68")
    TABLE = ("  consys_ic_ops                                  "
             "GLOBAL OBJECT  3     0x110        172")
    check("gate matches a STRONG provider", bool(rx_icops.match(STRONG)), STRONG.strip())
    check("gate rejects the __weak stub", not rx_icops.match(WEAKLINE), WEAKLINE.strip())
    check("gate matches the IC ops table", bool(rx_table.match(TABLE)), TABLE.strip())

# (e) Functional test of the symbol parser on a real module, plus a negative
#     control on the pre-fix artifact when it is still on disk.
SYMBOLS = ROOT / "port" / "elf_symbols.py"
STOCK = ROOT / "port" / "_device_backup" / "modules_stock" / "wmt_drv.ko"
if not SYMBOLS.exists() or not STOCK.exists():
    print("   SKIP (need port/elf_symbols.py and the stock vendor module)")
else:
    r = subprocess.run([EXE, str(SYMBOLS), str(STOCK),
                        "--grep", r"^mtk_wcn_get_consys_ic_ops$|^consys_ic_ops$"],
                       capture_output=True, text=True)
    check("elf_symbols.py reads ELF32 and finds the stock strong provider",
          r.returncode == 0 and "GLOBAL FUNC" in r.stdout
          and re.search(r"^  consys_ic_ops\s+GLOBAL OBJECT", r.stdout, re.M) is not None,
          (r.stdout + r.stderr).strip().replace("\n", " | ")[:160])
    PRE = (ROOT / "_ci" / "37190391324" / "kernel" / "out-archytas"
           / "connectivity-modules" / "wmt_drv.ko")
    if not PRE.exists():
        print("   SKIP negative control (pre-fix artifact not downloaded)")
    else:
        r = subprocess.run([EXE, str(SYMBOLS), str(PRE),
                            "--grep", r"^mtk_wcn_get_consys_ic_ops$|^consys_ic_ops$"],
                           capture_output=True, text=True)
        out = r.stdout
        check("negative control: the pre-fix build shows WEAK + no IC ops table",
              "WEAK" in out and re.search(r"^  consys_ic_ops\s", out, re.M) is None,
              out.strip().replace("\n", " | ")[:160])

print("\n== 9. wlan core chip FAMILY (CONNAC) + driver-data gate ==")
_i_bwl = next((li for li, d in _builds if d == "wlan"), None)
_i_bad = next((li for li, d in _builds if d == "wlan/adaptor"), None)
_i_g2 = next((i for i, l in enumerate(_logical)
              if "CONNAC chip layer present" in l), None)
_i_g4 = next((i for i, l in enumerate(_logical)
              if "mtk_axi_ids[] must be non-NULL" in l), None)

check("the chip FAMILY (CONNAC) is passed to the wlan core build",
      bool(re.search(r"build_mod wlan\b[\s\S]{0,140}?MTK_COMBO_CHIP=CONNAC", text)))
check("the part number no longer stands in for the family",
      "MTK_COMBO_CHIP=MT6761" not in text,
      "a bare part number selects no chip layer at all")
check("WLAN_CHIP_ID is set explicitly, as the vendor Android.mk does",
      bool(re.search(r"build_mod wlan\b[\s\S]{0,200}?WLAN_CHIP_ID=MT6761", text)))
check("chips/connac/connac.o gate runs after build_mod wlan, before the next module",
      None not in (_i_bwl, _i_g2, _i_bad) and _i_bwl < _i_g2 < _i_bad,
      "wlan@%s gate@%s next@%s" % (_i_bwl, _i_g2, _i_bad))
check("driver-data symbol gate runs after the strip (gates the shipped .ko)",
      None not in (_i_g4, _i_strip) and _i_strip < _i_g4,
      "strip@%s gate@%s" % (_i_strip, _i_g4))
check("driver-data gate goes through port/elf_symbols.py",
      _i_g4 is not None and "elf_symbols.py" in "\n".join(_logical[_i_g4 + 1: _i_g4 + 3]))

_rxw = re.findall(r"grep -qE '(\^  mt66xx_driver_data_[^']+)'", text)
check("driver-data gate regex is present", len(_rxw) == 1, "found %d" % len(_rxw))
if len(_rxw) == 1:
    rxw = re.compile(_rxw[0])
    VENDOR = ("  mt66xx_driver_data_connac                      "
              "GLOBAL OBJECT  3     0x3c60       4")
    EMPTY = "  -- 0 matching symbol(s)"
    check("gate matches a real driver-data object", bool(rxw.match(VENDOR)),
          VENDOR.strip())
    check("gate rejects an empty driver-data table", not rxw.match(EMPTY),
          EMPTY.strip())

# Functional test on real modules: the vendor's 32-bit wlan core has the object,
# our pre-fix aarch64 build does not -- which is exactly the panic condition.
WSTOCK = ROOT / "port" / "_device_backup" / "modules_stock" / "wlan_drv_gen4m.ko"
PREW = ROOT / "_ci" / "37193565582" / "wlan_drv_gen4m.ko"
if not SYMBOLS.exists() or not WSTOCK.exists():
    print("   SKIP (need port/elf_symbols.py and the stock vendor wlan module)")
else:
    r = subprocess.run([EXE, str(SYMBOLS), str(WSTOCK),
                        "--grep", r"^mt66xx_driver_data_"], capture_output=True, text=True)
    check("stock vendor wlan core carries mt66xx_driver_data_* (control: PASS)",
          r.returncode == 0 and "GLOBAL OBJECT" in r.stdout,
          (r.stdout + r.stderr).strip().replace("\n", " | ")[:160])
    if not PREW.exists():
        print("   SKIP negative control (pre-fix wlan artifact not downloaded)")
    else:
        r = subprocess.run([EXE, str(SYMBOLS), str(PREW),
                            "--grep", r"^mt66xx_driver_data_"],
                           capture_output=True, text=True)
        check("negative control: the pre-fix wlan core has NO driver data",
              "matching symbol(s)" in r.stdout
              and re.search(r"^  mt66xx_driver_data_", r.stdout, re.M) is None,
              r.stdout.strip().replace("\n", " | ")[:160])

print("\n== 10. Wi-Fi DTB: reserved-memory DMA pool for the Gen4M WLAN driver ==")
# The Wi-Fi ROM DTB has no "memory-region" on wifi@18000000 and declares
# reserved-memory/wifi-reserve-memory as a plain no-map MTK region. The Gen4M
# core calls of_reserved_mem_device_init() from axiDmaSetup(), gets -ENODEV and
# bails out before mtk_wcn_wmt_wlan_reg() -- every module insmods RC=0, nothing
# panics, and wlan0 just never shows up. patch_wifi_dtb.py closes both gaps.
DTB_IN = ROOT / "port" / "wifi_stock.dtb"
DTB_REF = ROOT / "port" / "4g_stock.dtb"
if not DTB_IN.exists():
    print("   SKIP (port/wifi_stock.dtb missing)")
else:
    sys.path.insert(0, str(ROOT / "port"))
    import patch_wifi_dtb as _pw

    # negative control: the unpatched ROM DTB must fail these checks
    _raw = _pw.Fdt(DTB_IN.read_bytes())
    _raw_wifi = _raw.root.child(_pw.WIFI_NODE)
    _raw_rm = _pw.find_path(_raw.root, _pw.RESERVED_MEMORY_NODE).child(
        _pw.WIFI_RMEM_NODE)
    check("control: stock DTB has NO memory-region on wifi@18000000",
          _raw_wifi.get("memory-region") is None, "present already?!")
    check("control: stock DTB node is not a shared-dma-pool",
          b"shared-dma-pool" not in (_raw_rm.get("compatible") or b""),
          repr(_raw_rm.get("compatible")))

    _out, _changed = _pw.patch(DTB_IN.read_bytes())
    check("patch reports a change", _changed is True)
    _pat = _pw.Fdt(_out)
    _pw_wifi = _pat.root.child(_pw.WIFI_NODE)
    _pw_rm = _pw.find_path(_pat.root, _pw.RESERVED_MEMORY_NODE).child(
        _pw.WIFI_RMEM_NODE)

    check("compatible gains shared-dma-pool",
          _pw_rm.get("compatible") == _pw.WIFI_RMEM_COMPAT,
          repr(_pw_rm.get("compatible")))
    check("reg is the fixed 3 MiB window",
          _pw_rm.get("reg") == struct.pack(">IIII", 0, _pw.WIFI_RMEM_BASE, 0,
                                           _pw.WIFI_RMEM_SIZE),
          _pw_rm.get("reg").hex())
    check("alloc-ranges dropped (reg takes over)",
          _pw_rm.get("alloc-ranges") is None)
    # no-map MUST stay: it is what routes the node past rmem_cma_setup() to
    # rmem_dma_setup(), the only one that installs rmem->ops.
    check("no-map kept (routes to rmem_dma_setup, not CMA)",
          _pw_rm.get("no-map") is not None)
    check("phandle assigned and bound from wifi@18000000",
          _pw_wifi.get("memory-region") is not None
          and _pw_wifi.get("memory-region") == _pw_rm.get("phandle"),
          (_pw_rm.get("phandle") or b"").hex())
    check("idempotent (second run is a no-op)",
          _pw.patch(_out) == (_out, False))

    # ---- consys-reserve-memory: same fix, different root cause -----------
    # The stock DTB also leaves this node with alloc-ranges (dynamic), so the
    # kernel's reserved-memory allocator picks some address in 0x40000000..
    # 0xC0000000.  WMT/bootloader, however, hard-code the CONSYS EMI STP
    # mailbox at 0xbf000000.  Two sides, two addresses -> gRxCount = -1 on
    # every STP packet -> HOST_AWAKE / FW_START handshake timeouts -> wlan0
    # never appears.  The fix pins it to 0xbf000000 (4 MiB, no overlap with
    # any bootloader-reserved window in 0x7xxx_xxxx).  Zero size growth
    # relative to the wifi-only patch: alloc-ranges(16 B) <-> reg(16 B),
    # strings table unchanged ("reg" already present).
    _raw_cs = _pw.find_path(_raw.root, _pw.RESERVED_MEMORY_NODE).child(
        _pw.CONSYS_RMEM_NODE)
    _pat_cs = _pw.find_path(_pat.root, _pw.RESERVED_MEMORY_NODE).child(
        _pw.CONSYS_RMEM_NODE)
    check("control: stock consys-reserve-memory has NO reg",
          _raw_cs is not None and _raw_cs.get("reg") is None)
    check("control: stock consys still has alloc-ranges",
          _raw_cs is not None and _raw_cs.get("alloc-ranges") is not None)
    check("consys gains fixed reg at 0xbf000000",
          _pat_cs is not None and _pat_cs.get("reg") == struct.pack(
              ">IIII", 0, _pw.CONSYS_RMEM_BASE, 0, _pw.CONSYS_RMEM_SIZE),
          (_pat_cs.get("reg") or b"").hex())
    check("consys alloc-ranges dropped (reg takes over)",
          _pat_cs is not None and _pat_cs.get("alloc-ranges") is None)
    check("consys compatible unchanged (mediatek,consys-reserve-memory)",
          _pat_cs is not None
          and _pat_cs.get("compatible") == _pw.CONSYS_RMEM_COMPAT,
          repr(_pat_cs.get("compatible")))
    check("consys no-map kept",
          _pat_cs is not None and _pat_cs.get("no-map") is not None)

    # make_wifi_template.py anchors on this node name
    check("aw87329_pa anchor survives", b"aw87329_pa" in _out)
    # Board-specific charger limits must NOT be inherited from the 4G DTB. Compare
    # the /charger node itself -- a raw byte search is useless here because e.g.
    # 0x00100590 legitimately appears once in /lk_charger of the Wi-Fi DTB too.
    def _charger(fdt):
        n = fdt.root.child("charger")
        return dict(n.props) if n is not None else {}

    def _lk_charger(fdt):
        n = fdt.root.child("lk_charger")
        return dict(n.props) if n is not None else {}

    _c_wifi, _c_pat = _charger(_raw), _charger(_pat)
    check("board-specific /charger values untouched by the patch",
          _c_pat == _c_wifi
          and _c_wifi.get("ac_charger_input_current") == struct.pack(">I", 0x10C8E0)
          and _c_wifi.get("charging_host_charger_current") == struct.pack(">I", 0x7A120),
          "patched ac=%s host=%s"
          % ((_c_pat.get("ac_charger_input_current") or b"").hex(),
             (_c_pat.get("charging_host_charger_current") or b"").hex()))
    if DTB_REF.exists():
        _c_ref = _charger(_pw.Fdt(DTB_REF.read_bytes()))
        check("...and they really do differ from the 4G board's",
              _c_ref.get("ac_charger_input_current")
              != _c_wifi.get("ac_charger_input_current"),
              "4g ac=%s" % (_c_ref.get("ac_charger_input_current") or b"").hex())
        check("the patch leaves /lk_charger alone as well",
              _lk_charger(_pat) == _lk_charger(_raw))
    if DTB_REF.exists():
        # The reference is the vendor's *own* fixed-up DTB for the same SoC, so
        # after patching, the only textual difference may be the two charger
        # values. Compare structurally, ignoring property order.
        def _nodes(fdt, *path):
            return _pw.find_path(fdt.root, *path)

        _ref = _pw.Fdt(DTB_REF.read_bytes())
        for _p in (("reserved-memory", "wifi-reserve-memory"), (_pw.WIFI_NODE,)):
            _a = _nodes(_pat, *_p)
            _b = _nodes(_ref, *_p)
            check("matches 4g_stock.dtb on /%s" % "/".join(_p),
                  sorted(_a.props) == sorted(_b.props),
                  "patched=%r ref=%r" % (sorted(_a.props), sorted(_b.props)))
        # Size parity is load-bearing, not cosmetic: the 32 MiB boot template has
        # no slack, so a DTB even 2 bytes larger makes make_wifi_template.py emit a
        # 32 MiB + N image and package_archimedes_boot.py then dies with the
        # misleading "template must be an exact 32 MiB Android boot image".
        # That is exactly how run #37195577635 failed (33554542-byte template),
        # after the emitter rebuilt the strings block (+123 B) and padded the blob
        # (+2 B) instead of reusing the original strings block and emitting an
        # unpadded blob.
        _ref_len = DTB_REF.stat().st_size
        check("patched DTB is no larger than the template's DTB", len(_out) <= _ref_len,
              "patched=%d ref=%d" % (len(_out), _ref_len))
        check("...in fact exactly the same size", len(_out) == _ref_len,
              "patched=%d ref=%d" % (len(_out), _ref_len))
    else:
        print("   SKIP 4g_stock.dtb comparison (reference DTB missing)")

# The packager must fail loudly instead of silently growing the image: a bytearray
# slice assignment past the end EXTENDS it, so the 32 MiB invariant is lost without
# any error at the point of the mistake.
_tpl_py = (ROOT / "port" / "make_wifi_template.py").read_text(encoding="utf-8")
check("make_wifi_template.py guards against an oversized DTB",
      "board DTB is" in _tpl_py and "len(new_region) > ks" in _tpl_py)
check("make_wifi_template.py asserts the 32 MiB invariant",
      "repacked image is" in _tpl_py)

# The workflow must patch the DTB and package the patched one.
_wf = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""
check("workflow runs port/patch_wifi_dtb.py",
      "patch_wifi_dtb.py" in _wf)
check("workflow packages port/wifi_archytas.dtb",
      "wifi-dtb port/wifi_archytas.dtb" in _wf)
check("workflow no longer packages the unpatched DTB",
      "wifi-dtb port/wifi_stock.dtb" not in _wf)

# ---------------------------------------------------------------- group 11 --
# End-to-end size rehearsal against the REAL CI template.
#
# The 32 MiB boot template is byte-exact: its kernel region is exactly the gzip
# plus the 4G board DTB, with ZERO slack. Swapping in the Wi-Fi DTB is therefore
# only possible because the patched DTB is exactly as large as the 4G one. Groups
# 1-10 check the pieces; this group actually runs the two scripts the way the
# workflow does, which is what would have caught run #37195577635 locally instead
# of on CI (it failed with a 33554542-byte template and the misleading
# "template must be an exact 32 MiB Android boot image").
print("\n== 11. end-to-end size rehearsal against the real 32 MiB template ==")

# Locally the agui4541/archimedes-kernel-64 checkout sits at
# archimedes-kernel-64-main/; the workflow checks the same repo out at kernel/.
TPL = next((p for p in (
    ROOT / "archimedes-kernel-64-main" / "tools"
    / "archimedes-boot-template-32MiB.img",
    ROOT / "kernel" / "tools" / "archimedes-boot-template-32MiB.img",
) if p.exists()), ROOT / "archimedes-kernel-64-main" / "tools"
    / "archimedes-boot-template-32MiB.img")


def _trailing_fdt(blob):
    """Same rule as make_wifi_template.trailing_fdt: last FDT that ends the blob."""
    pos = blob.rfind(b"\xd0\x0d\xfe\xed")
    while pos >= 0:
        if pos + 8 <= len(blob):
            size = struct.unpack_from(">I", blob, pos + 4)[0]
            if size >= 40 and pos + size == len(blob):
                return pos, size
        pos = blob.rfind(b"\xd0\x0d\xfe\xed", 0, pos)
    return None


if not TPL.exists():
    print("   SKIP rehearsal (agui checkout absent: %s)" % TPL.name)
elif "_out" not in globals():
    print("   SKIP rehearsal (port/wifi_stock.dtb missing)")
else:
    _tpl = TPL.read_bytes()
    _ks = struct.unpack_from("<I", _tpl, 8)[0]
    check("CI template is an exact 32 MiB Android boot image",
          len(_tpl) == 33554432 and _tpl[:8] == b"ANDROID!", "len=%d" % len(_tpl))
    _tf = _trailing_fdt(_tpl[2048:2048 + _ks])
    check("CI template carries a trailing board DTB", _tf is not None,
          "kernel_region=%d" % _ks)
    if _tf:
        check("CI template's DTB is exactly the patched DTB's size",
              _tf[1] == len(_out), "template=%d patched=%d" % (_tf[1], len(_out)))
        with tempfile.TemporaryDirectory() as _td:
            _tdp = Path(_td)
            (_tdp / "wifi_archytas.dtb").write_bytes(_out)
            _r = subprocess.run(
                [EXE, str(ROOT / "port" / "make_wifi_template.py"),
                 "--template", str(TPL),
                 "--wifi-dtb", str(_tdp / "wifi_archytas.dtb"),
                 "--output", str(_tdp / "tpl.img")],
                capture_output=True, text=True)
            check("make_wifi_template.py accepts the patched DTB on the real template",
                  _r.returncode == 0, (_r.stderr or _r.stdout).strip()[:200])
            _img = _tdp / "tpl.img"
            _isz = _img.stat().st_size if _img.exists() else -1
            check("...and emits exactly 32 MiB (not 32 MiB + N)",
                  _isz == 33554432, "size=%d" % _isz)
            if _isz == 33554432:
                _o = _img.read_bytes()
                _of = _trailing_fdt(_o[2048:2048 + _ks])
                check("...with the patched DTB intact inside",
                      _of is not None
                      and _o[2048 + _of[0]:2048 + _of[0] + _of[1]] == _out)
                check("...and the kernel region length unchanged",
                      struct.unpack_from("<I", _o, 8)[0] == _ks)
            # Negative control: a VALID FDT one byte larger must be rejected. It has
            # to be a real FDT (totalsize bumped) or trailing_fdt would reject it for
            # the wrong reason. Without the guard, bytearray slice assignment silently
            # EXTENDS the image and the 32 MiB invariant is lost with no error here.
            _big = bytearray(_out) + b"\x00"
            struct.pack_into(">I", _big, 4, len(_big))
            (_tdp / "too_big.dtb").write_bytes(bytes(_big))
            _r2 = subprocess.run(
                [EXE, str(ROOT / "port" / "make_wifi_template.py"),
                 "--template", str(TPL),
                 "--wifi-dtb", str(_tdp / "too_big.dtb"),
                 "--output", str(_tdp / "big.img")],
                capture_output=True, text=True)
            _msg = (_r2.stderr + _r2.stdout)
            check("control: a 1-byte-larger DTB is rejected by the size guard",
                  _r2.returncode != 0 and "board DTB is" in _msg,
                  _msg.strip().splitlines()[-1][:160] if _r2.returncode else
                  "ACCEPTED -- guard missing!")
            check("control: no oversize image is written",
                  not (_tdp / "big.img").exists())

# ---------------------------------------------------------------- group 12 --
# CI trigger hygiene. A push that only touches docs, this harness or the offline
# analysis tools cannot change the build output, so it must not burn an ~11 min
# runner. The risk in the other direction is worse though: silently ignoring a
# file the build actually reads would leave a stale boot image looking green, so
# both halves of that contract are asserted here.
print("\n== 12. CI trigger hygiene (paths-ignore) ==")


def _gh_match(path, pat):
    """Approximate GitHub's paths-ignore matching (**/ also matches zero dirs)."""
    if fnmatch.fnmatch(path, pat):
        return True
    while pat.startswith("**/"):
        pat = pat[3:]
        if fnmatch.fnmatch(path, pat):
            return True
    return False


_pi = []
_in = False
for _line in _wf.splitlines():
    _s = _line.strip()
    if _s.startswith("paths-ignore:"):
        _in = True
        continue
    if _in:
        if _s.startswith("- "):
            _pi.append(_s[2:].strip().strip("'\""))
        elif _s and not _s.startswith("#"):
            break                      # next top-level key -> end of the block

check("workflow filters the push trigger (paths-ignore)", bool(_pi),
      "%d pattern(s): %s" % (len(_pi), ", ".join(_pi)))

# The fast lane is dispatched by hand and must stay runnable; pull_request should
# keep validating every PR. So the filter may only be attached to push.
_push_block = _wf.split("push:", 1)[1].split("pull_request:", 1)[0] \
    if "push:" in _wf and "pull_request:" in _wf else ""
check("paths-ignore is attached to push only",
      "paths-ignore" in _push_block)

# Anything the build reads must NEVER be matched. A stale-but-green image is the
# worst possible outcome, so this list is the guard rail, not decoration.
_BUILD_INPUTS = [
    "port/patch_adrp_reloc.py",
    "port/patch_wifi_dtb.py",
    "port/make_wifi_template.py",
    "port/elf_symbols.py",
    "port/elf_debug_share.py",
    "port/build_connectivity_modules.sh",
    "port/k61v1_64_archytas_defconfig",
    "port/wifi_stock.dtb",
    "port/4g_stock.dtb",
    "port/flash_archytas.sh",
    ".github/workflows/build.yml",
]
_bad = [(b, p) for b in _BUILD_INPUTS for p in _pi if _gh_match(b, p)]
check("no build input is matched by paths-ignore", not _bad, repr(_bad))

# ...and the things we set out to skip must really be skipped.
_SKIP = [
    "port/verify_build_script.py",
    "port/ci_logs.py",
    "port/ci_artifact.py",
    "port/dtb_dump.py",
    "port/dtb_diff.py",
    "port/analyze_oem_diff.py",
    "port/ARCHYTAS_PORT.md",
    "HANDOFF.md",
]
_missed = [s for s in _SKIP if not any(_gh_match(s, p) for p in _pi)]
check("docs / self-check / CI helpers / analysis tools are all skipped",
      not _missed, "not covered: %s" % ", ".join(_missed))

print("\n================ %s ================" % (
    "ALL CHECKS PASS" if not failures else "FAILURES: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
