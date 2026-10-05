#!/usr/bin/env bash
#
# Build the four MediaTek connectivity kernel modules for the Archytas (Wi-Fi)
# MT6761 / k61 AArch64 4.9.117 kernel, using agui's pinned external sources
# plus his 4.9 compatibility patch set.
#
# The kernel itself is built elsewhere (build.yml -> Image.gz-dtb). This script
# only compiles the out-of-tree .ko files that the retail vendor image ships as
# 32-bit ARMv7 modules (which cannot load into an AArch64 kernel).
#
# Layout produced under the kernel tree (matches agui's in-tree Kbuild plumbing
# in drivers/misc/mediatek/connectivity/Makefile, which expects the standalone
# repos at vendor/mediatek/kernel_modules/connectivity/...):
#
#   vendor/mediatek/kernel_modules/connectivity/common      -> wmt_drv.ko
#   vendor/mediatek/kernel_modules/connectivity/wlan        -> wlan_mt6761_axi.ko
#        (derived as wlan_<lower(WLAN_CHIP_ID)>_<HIF>; WLAN_CHIP_ID=word1(MTK_COMBO_CHIP)=MT6761)
#   vendor/mediatek/kernel_modules/connectivity/wlan/adaptor-> wmt_chrdev_wifi.ko
#   vendor/mediatek/kernel_modules/connectivity/bt          -> bt_drv.ko
#
# Usage: bash port/build_connectivity_modules.sh [<KERNEL_SRC> <KERNEL_OUT>]
#                                        [--modules-only]
#
# --modules-only / MODULES_ONLY=1
#   The fast lane. Use it when the boot image is already built/flashed and you
#   only need to re-iterate on the four .ko files: it will NOT build the kernel
#   Image, so a round costs ~2-3 min instead of ~10. If the kernel out-tree is
#   not prepared yet (no include/generated/autoconf.h) it configures the tree
#   and runs modules_prepare itself, which is enough for out-of-tree (M=) builds.
#
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PORT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Normalize KROOT/KOUT to ABSOLUTE paths. Kbuild resolves O= relative to the
# directory entered via 'make -C $KROOT', so a relative O= would be re-based as
# $KROOT/$KOUT (e.g. kernel/kernel/out-archytas) and the prebuilt .config there
# would be missing -> "Configuration file .config not found" at modules_prepare.
abspath() { ( cd "$(dirname "$1")" 2>/dev/null && echo "$(pwd)/$(basename "$1")" ) || echo "$1"; }

MODULES_ONLY="${MODULES_ONLY:-0}"
_pos=()
for _a in "$@"; do
  case "$_a" in
    --modules-only) MODULES_ONLY=1 ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) _pos+=("$_a") ;;
  esac
done
KROOT="$(abspath "${_pos[0]:-$ROOT/kernel}")"
KOUT="$(abspath "${_pos[1]:-$ROOT/kernel/out-archytas}")"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"

CONN="vendor/mediatek/kernel_modules/connectivity"
AUTOCONF="$KOUT/include/generated/autoconf.h"
DEFCONFIG=k61v1_64_archytas_defconfig

echo "== build_connectivity_modules =="
echo "KROOT = $KROOT"
echo "KOUT  = $KOUT"
echo "CROSS = $CROSS_COMPILE"
echo "MODE  = $([ "$MODULES_ONLY" = 1 ] && echo 'modules-only (fast lane)' || echo 'with-kernel')"
command -v git  >/dev/null 2>&1 || { echo "ERROR: git required"; exit 1; }
command -v patch >/dev/null 2>&1 || echo "WARN: patch not found; relying on git apply only"

# ---- 0. Make sure the kernel out-tree is usable for an M= build ------------
# A full CI run builds Image.gz-dtb first (see .github/workflows/build.yml),
# which leaves a fully prepared out-tree behind. In --modules-only mode we skip
# that multi-minute kernel compile, so if the out-tree is cold we configure it
# and let step 3's `modules_prepare` generate everything an M= build needs.
prepare_kernel_tree() {
  local defcfg="$KROOT/arch/arm64/configs/$DEFCONFIG"
  [ -f "$defcfg" ] || { echo "ERROR: defconfig missing: $defcfg"; exit 1; }
  echo ">> out-tree is cold -> configuring $DEFCONFIG + modules_prepare"
  mkdir -p "$KOUT/include"
  ln -sfn "$KROOT/include/dt-bindings" "$KOUT/include/dt-bindings"
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       "$DEFCONFIG"
  local KCONFIG="$KROOT/scripts/config"
  [ -x "$KCONFIG" ] || KCONFIG="perl $KCONFIG"
  $KCONFIG --file "$KOUT/.config" --disable CONFIG_KSU
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       olddefconfig
}

if [ ! -f "$AUTOCONF" ]; then
  if [ "$MODULES_ONLY" = 1 ]; then
    command -v make >/dev/null 2>&1 || { echo "ERROR: make required"; exit 1; }
    prepare_kernel_tree
  else
    echo "ERROR: $AUTOCONF missing; build the kernel (modules_prepare) first"
    echo "       (or re-run with --modules-only to prepare the tree here)"
    exit 1
  fi
fi

# ---- 1. Fetch the four pinned external repos -------------------------------
# Map of abbreviated -> full 40-hex SHAs (fallback if the GitHub API is
# unreachable). A bare/abbreviated SHA CANNOT be fetched by Git (the wire
# protocol needs the full object id), so we must expand it before fetching.
declare -A FULLSHA=(
  [364afcf]=364afcfde5c4d952fb67705aacc26456c64eb9dd
  [e84d47e]=e84d47e835553a6c1f654309341cebe78b96be67
  [9074126]=90741260b236d9fc808e9c7ff2a650977242833e
  [ba2c5a5]=ba2c5a57fc22c7e9dc6224b2cee8d8a1c6d98e80
)

fetch_repo() {
  local url="$1" commit="$2" dest="$3"
  echo ">> fetch $url @ $commit"
  echo "   -> $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  if ! git clone --depth 1 "$url" "$dest" >/dev/null 2>&1; then
    echo "ERROR: clone failed for $url"; exit 1
  fi
  # Expand the (possibly abbreviated) commit SHA to its full 40-hex form. Try the
  # GitHub API first (authoritative), then the hardcoded table, then assume the
  # given value is already full. Without the full SHA, 'git fetch <sha>' is
  # rejected by the server and the build silently used the clone tip -- whose
  # default branch did NOT contain the module Makefile (build broke with
  # "No rule to make target .../Makefile").
  local full="$commit"
  if ! [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
    if command -v curl >/dev/null 2>&1; then
      local api="${url%.git}"
      api="${api#https://github.com/}"
      api="https://api.github.com/repos/${api}/commits/${commit}"
      local api_sha=""
      # MUST NOT be able to abort the build. This runs under `set -e` +
      # `set -o pipefail`, so `curl -f` returning exit 22 (HTTP >=400, e.g. the
      # unauthenticated GitHub API rate limit) propagated straight out of the
      # script and killed CI build #14 before the FULLSHA fallback below could
      # run. Use a non-fatal curl (no -f) with a timeout, and swallow any
      # residual failure so an unreachable/limited API just falls through to
      # the pinned table.
      api_sha="$(curl -sSL --max-time 20 "$api" 2>/dev/null \
                 | sed -n 's/^ *"sha": *"\([0-9a-f]\{40\}\)".*/\1/p' | head -1)" || api_sha=""
      if [ -n "$api_sha" ]; then
        echo "   api: $commit -> $api_sha"
      else
        echo "   api lookup returned nothing for $commit (rate limit/offline?); using pinned table"
      fi
      [ -n "$api_sha" ] && full="$api_sha"
    fi
    [ "$full" = "$commit" ] && [ -n "${FULLSHA[$commit]:-}" ] && full="${FULLSHA[$commit]}"
  fi
  echo "   using $full"
  # Fetch the exact commit (GitHub serves any reachable commit by full SHA).
  if git -C "$dest" fetch --depth 1 origin "$full" >/dev/null 2>&1 \
     && git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"; return 0
  fi
  if git -C "$dest" fetch origin "$full" >/dev/null 2>&1 \
     && git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"; return 0
  fi
  git -C "$dest" fetch --unshallow origin >/dev/null 2>&1 || true
  if git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"
  else
    echo "WARN: could not checkout $commit in $dest (using clone tip)"
  fi
}

WLAN_DIR="$KROOT/$CONN/wlan"
fetch_repo https://github.com/zainarbani/vendor-mediatek-kernel_modules-connectivity-wlan-core-gen4m \
          ba2c5a5 "$WLAN_DIR"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-common \
          364afcf "$KROOT/$CONN/common"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-wlan-adaptor \
          e84d47e "$KROOT/$CONN/wlan/adaptor"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-bt-mt66xx \
          9074126 "$KROOT/$CONN/bt"

# ---- 1b. Align the common WMT struct with the newer Gen4M WLAN core --------
# Version skew: the Gen4M WLAN core (zainarbani @ ba2c5a5) is newer than the
# pinned common repo (Motorola @ 364afcf). It registers the WMT wlan callback
# 'wlan_is_wifi_drv_own_cb' (hifAxiIsWifiDrvOwn in axi.c -- both in the original
# mtk_axi_probe and in agui's axiWmtRetry), but the common's wmt_exp.h struct
# MTK_WCN_WMT_WLAN_CB_INFO only has 5 members -> "has no member named
# 'wlan_is_wifi_drv_own_cb'". Append the missing member (a plain function
# pointer, same style as the peers just above it) so the WLAN module compiles.
# Appending keeps every existing member offset unchanged, and the (older)
# wmt_drv simply never invokes the extra callback -> harmless at runtime.
WMT_EXP="$KROOT/$CONN/common/common_main/include/wmt_exp.h"
if [ -f "$WMT_EXP" ] && ! grep -q "wlan_is_wifi_drv_own_cb" "$WMT_EXP"; then
  awk '{ if ($0 ~ /^\} MTK_WCN_WMT_WLAN_CB_INFO/) print "\tINT32 (*wlan_is_wifi_drv_own_cb)(VOID);"; print }' \
      "$WMT_EXP" > "$WMT_EXP.new" && mv "$WMT_EXP.new" "$WMT_EXP"
  echo "   added wlan_is_wifi_drv_own_cb to MTK_WCN_WMT_WLAN_CB_INFO"
else
  echo "WARN: $WMT_EXP missing or already patched"
fi

# ---- 2. Apply agui's 4.9 compatibility patches to the WLAN core ------------
PATCH1="$KROOT/vendor/archimedes-wlan/0001-mt6761-prealloc-axi-dma.patch"
PATCH2="$KROOT/Documentation/wifi-coherent-dma-58c7ba5.patch"
for p in "$PATCH1" "$PATCH2"; do
  [ -f "$p" ] || { echo "WARN: patch not found: $p (skipping)"; continue; }
  echo ">> apply $(basename "$p")"
  if git -C "$KROOT" apply --3way --directory="$CONN/wlan" "$p" 2>/dev/null; then
    echo "   applied via git apply"
  elif patch -p1 -d "$WLAN_DIR" < "$p" 2>/dev/null; then
    echo "   applied via patch"
  else
    echo "ERROR: failed to apply $(basename "$p")"; exit 1
  fi
done

# ---- 2b. Neutralize -Werror in the out-of-tree module Makefiles ------------
# These old MTK modules (common, wlan-core, ...) add a bare 'ccflags-y += -Werror'
# to their Makefiles. Under GCC 11 the benign pointer-sign warnings that the 4.9
# kernel headers now emit become hard errors ("cc1: all warnings being treated
# as errors") and the build fails. Our KCFLAGS=-Wno-error is prepended BEFORE the
# module's ccflags-y, so the module's -Werror wins (GCC last-flag-wins); strip it
# from every Makefile under the connectivity tree instead.
echo ">> neutralize -Werror in module Makefiles"
# Match Makefile, Kbuild (kernel modules often use 'Kbuild' as the makefile
# name, e.g. under os/linux/) and *.mk -- any of these can carry a bare
# 'ccflags-y += -Werror' that escalates benign warnings to hard errors.
find "$KROOT/$CONN" -type f \( -name 'Makefile' -o -name 'Kbuild' -o -name '*.mk' \) -print0 \
  | while IFS= read -r -d '' m; do
      sed -i -E 's/-Werror=[A-Za-z0-9_+-]+//g; s/-Werror([[:space:]]|$)/ /g' "$m"
    done

# ---- 2c. ARCHYTAS EMI/MPU protection compat shim ------------------------
# WHY THIS EXISTED BEFORE: The Gen4M WLAN module (gl_init.c, axi.c) references
# gConEmiPhyBaseFinal / gConEmiSizeFinal and kalSetEmiMpuProtection() (and
# kalSetDrvEmiMpuProtection() on the non-prealloc path).  All four are guarded
# by `#if CFG_MTK_ANDROID_EMI` in the module source, and that macro defaulted to
# 0 in standalone builds.  The result:
#   - wlanDownloadEMISection() in fw_dl.c compiled to an 8-byte stub that
#     silently returns WLAN_STATUS_SUCCESS -> EMI sections in the firmware
#     (whose tailers have DOWNLOAD_CONFIG_EMI set) were never written -> the
#     firmware ran with uninitialized EMI memory and crashed at EVA=0xF0400000
#     during FW_START (observed live 2026-10-05: stp_trace32_dump type=5
#     exception, gRxCount became 1 after the consys DTB fix, proving STP was
#     alive but firmware memory was corrupted).
#   - kalSetEmiMpuProtection() was only referenced inside EMI-guarded blocks,
#     so with the macro off, the linker never even needed it.
#   - gConEmiPhyBaseFinal / gConEmiSizeFinal were declared in gl_init.c only
#     under #if CFG_MTK_ANDROID_EMI, so they didn't exist either.
#
# THE FIX (2026-10-05): enable CFG_MTK_ANDROID_EMI=1 via -D in the wlan Makefile.
# This turns EMI section download and MPU protection back on.  A few follow-ons:
#   - gl_init.c (under the new `#if CFG_MTK_ANDROID_EMI`) now defines its OWN
#     gConEmiPhyBaseFinal / gConEmiSizeFinal.  The compat_stub.c versions must
#     become WEAK so the upstream strong ones win.
#   - kalSetEmiMpuProtection / kalSetDrvEmiMpuProtection are NOT defined
#     upstream at all (they come from a MTK HAL that this 4.9 tree lacks).
#     compat_stub.c still provides them, but now as REAL function bodies (the
#     module source will call them).  The MPU policy is owned by the
#     bootloader/conninfra on this device, so no-ops are fine.
#   - emi_compat.h stays: it declares everything, and gl_init.c now needs the
#     extern declarations for kalSetEmi*Protection since they're not defined
#     there.
#
# We inject -DCFG_MTK_ANDROID_EMI=1 via ccflags-y on the wlan Makefile, anchored
# on the agui -Wno-error line (same trick as the -mcmodel=large injection).

cat > "$WLAN_DIR/emi_compat.h" <<'EOF'
#ifndef ARCHYTAS_EMI_COMPAT_H
#define ARCHYTAS_EMI_COMPAT_H
#include <linux/types.h>

/* These two are defined in gl_init.c when CFG_MTK_ANDROID_EMI=1,
 * but we still need the extern declaration for callers in axi.c / fw_dl.c
 * who include this header instead of gl_init.c directly. */
extern phys_addr_t gConEmiPhyBaseFinal;
extern unsigned long long gConEmiSizeFinal;

/* These two are NEVER defined upstream -- they live in the MTK HAL that this
 * 4.9 tree lacks.  compat_stub.c provides the only implementation (no-ops). */
extern void kalSetEmiMpuProtection(phys_addr_t emiPhyBase, bool enable);
extern void kalSetDrvEmiMpuProtection(phys_addr_t emiPhyBase, uint32_t offset, uint32_t size);
#endif
EOF

cat >> "$WLAN_DIR/compat_stub.c" <<'EOF'

/* ARCHYTAS EMI/MPU compat shim -- see emi_compat.h.
 *
 * gConEmiPhyBaseFinal / gConEmiSizeFinal:
 *   gl_init.c defines strong versions of these under `#if CFG_MTK_ANDROID_EMI`.
 *   We supply __weak fallbacks so the file compiles even if the macro ends up
 *   off, while the upstream strong ones always win when it is on.
 *
 * kalSetEmiMpuProtection / kalSetDrvEmiMpuProtection:
 *   The MTK HAL that provides these is absent from this kernel tree.
 *   The bootloader/conninfra already configures the EMI MPU for the
 *   connectivity window, so no-ops are the right answer. */
__weak phys_addr_t gConEmiPhyBaseFinal;
__weak unsigned long long gConEmiSizeFinal;
void kalSetEmiMpuProtection(phys_addr_t emiPhyBase, bool enable)
{
    /* Bootloader owns the EMI MPU; we don't reconfigure it. */
}
void kalSetDrvEmiMpuProtection(phys_addr_t emiPhyBase, uint32_t offset, uint32_t size)
{
    /* Bootloader owns the EMI MPU; we don't reconfigure it. */
}
EOF

# Force-include emi_compat.h into every WLAN TU (anchored on agui's -Wno-error).
# Also inject -DCFG_MTK_ANDROID_EMI=1 -- this is the SWITCH that turns EMI
# section download (fw_dl.c) and MPU protection calls (axi.c, gl_init.c) back
# on.  Without it, wlanDownloadEMISection() compiled to an 8-byte stub that
# silently skipped EMI firmware sections, causing the firmware to crash at
# EVA=0xF0400000 during FW_START (observed live 2026-10-05).
if grep -q 'ccflags-y += -Wno-error' "$WLAN_DIR/Makefile"; then
  sed -i "s|^ccflags-y += -Wno-error|ccflags-y += -Wno-error\nccflags-y += -include $WLAN_DIR/emi_compat.h\nccflags-y += -DCFG_MTK_ANDROID_EMI=1|" "$WLAN_DIR/Makefile"
  echo "   force-include emi_compat.h + -DCFG_MTK_ANDROID_EMI=1 added to wlan Makefile"
else
  echo "WARN: -Wno-error anchor not found in wlan Makefile; EMI shims NOT injected"
fi

# ---- 2d. Force the large code model for every connectivity module -----------
# WHY: the target kernel is built with CONFIG_ARM64_ERRATUM_843419=y
# (Cortex-A53 erratum), which selects CONFIG_ARM64_MODULE_CMODEL_LARGE and makes
# arch/arm64/Makefile add KBUILD_CFLAGS_MODULE += -mcmodel=large. That flag IS
# reaching cc1 out of the box -- verified from the DW_AT_producer string baked
# into the shipped .ko:
#     GNU C89 11.4.0 -mlittle-endian -mgeneral-regs-only -mcmodel=large
#       -mabi=lp64 -g -O2 -std=gnu90 -fno-strict-aliasing -fno-common
#       -fno-asynchronous-unwind-tables -fno-pic -fstack-protector-strong ...
# so `-mcmodel=large` is a belt-and-braces re-assertion here, NOT the fix (an
# earlier revision of this script blamed KCFLAGS for not reaching cc1 -- that
# diagnosis was wrong).
#
# It is re-asserted through ccflags-y because ccflags-y is provably delivered to
# cc1 for these modules (the MTK Makefiles' own `ccflags-y += -Werror` used to
# abort the build), so this also documents the intended code model in the
# module's own Makefile. Appended last so it wins under GCC's last-flag-wins.
echo ">> force -mcmodel=large in module Makefiles"
for mf in "$KROOT/$CONN/common/Makefile" \
          "$KROOT/$CONN/wlan/Makefile" \
          "$KROOT/$CONN/wlan/adaptor/Makefile" \
          "$KROOT/$CONN/bt/Makefile"; do
  if [ ! -f "$mf" ]; then
    echo "WARN: module Makefile not found: $mf"
    continue
  fi
  if grep -q 'ARCHYTAS_MCMODEL_LARGE' "$mf"; then
    echo "   already patched: $mf"
    continue
  fi
  {
    printf '\n# ARCHYTAS_MCMODEL_LARGE --- injected by port/build_connectivity_modules.sh\n'
    printf '# CONFIG_ARM64_ERRATUM_843419=y => the module loader rejects ADRP\n'
    printf '# relocations, so the module must be built with the large code model.\n'
    printf 'ccflags-y += -mcmodel=large\n'
    printf 'subdir-ccflags-y += -mcmodel=large\n'
  } >> "$mf"
  echo "   injected -mcmodel=large into $mf"
done

# ---- 2e. Let the module loader process ADRP relocations (THE ADRP FIX) -----
# `-mcmodel=large` (step 2d) is applied, and it is still NOT enough: GCC 11 on
# aarch64 keeps emitting `adrp`+`ldr` pairs that read a 64-bit slot from a
# per-function LITERAL POOL placed inside .text itself (the `$d` mapping symbols
# GAS emits). Measured on the build #8 artifact (bt_drv.ko, 461496 B):
#     161 x R_AARCH64_ADR_PREL_PG_HI21 (275), ALL targeting "[sec].text" with
#     small, 8-byte-aligned addends that sit immediately after a function end
#     (0x1d8 right after BT_poll@0x100+0xd4, 0x2a8, 0x798, 0xcc0, ...), each
#     paired with a 64-bit pool slot that carries an R_AARCH64_ABS64 (257).
# Binutils' aarch64 linker enables --fix-cortex-a53-843419 BY DEFAULT, and
# arch/arm64/Makefile only adds `--no-fix-cortex-a53-843419` in the *else*
# branch, i.e. when the erratum is disabled -- so with
# CONFIG_ARM64_ERRATUM_843419=y the module link keeps its erratum fix and the
# surviving ADRP relocations are exactly what the kernel loader refuses:
#     module wmt_drv: unsupported RELA relocation: 275
#     insmod: failed to load ...: Exec format error
#
# No compiler flag makes these go away, so fix the one place that actually
# decides: arch/arm64/kernel/module.c compiles the ADRP handlers out behind
#     #ifndef CONFIG_ARM64_ERRATUM_843419
#         case R_AARCH64_ADR_PREL_PG_HI21_NC:
#         case R_AARCH64_ADR_PREL_PG_HI21: ...
#     #endif
# Drop that guard so ADRP relocations are relocated like every other type.
# The upstream intent (modules use the large model => they never need this) does
# not hold for these old MTK modules, and the reloc_insn_imm(RELOC_OP_PAGE,...)
# body itself is the standard, correct handler.
#
# Deliberately NOT done instead: disabling CONFIG_ARM64_ERRATUM_843419 in the
# defconfig. That would also drop --fix-cortex-a53-843419 from LDFLAGS_vmlinux
# and so remove the erratum workaround from the KERNEL ITSELF (real Cortex-A53
# hardware bug: "a load or store might access an incorrect address"). This
# surgical patch keeps the kernel binary's workaround intact and only relaxes
# the module-loader policy. Trade-off: the ADRPs inside the modules are then
# accepted verbatim, which is exactly what the (default-on) linker erratum fix
# already vetted.
#
# NOTE: this changes the KERNEL, so the boot image must be rebuilt and reflashed
# once; after that, --modules-only runs only need the .ko files.
#
# ORDERING MATTERS: the patch has to be in place before `make Image.gz-dtb` runs,
# otherwise the boot image in the artifact still carries the unpatched loader.
# build.yml therefore applies it in its own step before the kernel build; the
# call below is the idempotent safety net for --modules-only runs (where no
# kernel is built at all) and keeps the source-consistency gate below meaningful.
echo ">> allow ADRP relocations in arch/arm64/kernel/module.c"
MODULE_C="$KROOT/arch/arm64/kernel/module.c"
python3 "$PORT/patch_adrp_reloc.py" "$KROOT"
if ! grep -q "ARCHYTAS_ADRP_RELOC" "$MODULE_C"; then
  echo "ERROR: ADRP relocation patch did not apply; modules with ADRP would be rejected"
  exit 1
fi
if grep -q '^#ifndef CONFIG_ARM64_ERRATUM_843419' "$MODULE_C"; then
  echo "ERROR: a '#ifndef CONFIG_ARM64_ERRATUM_843419' is still present in $MODULE_C"
  exit 1
fi

# ---- 3. Prepare generated headers / vmlinux symtab for out-of-tree modpost
echo ">> modules_prepare"
make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
     HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
     KCFLAGS="-Wno-error" modules_prepare -j"$JOBS"

# ---- 3b. Resolve the two make variables common/Makefile needs ---------------
# THIS IS THE Wi-Fi FIX.  common/Makefile gates its chip file on a pattern that
# carries LITERAL DOUBLE QUOTES:
#
#   ifneq ($(filter "CONSYS_%",$(CONFIG_MTK_COMBO_CHIP)),)          # line 198
#   $(MODULE_NAME)-objs += common_main/platform/$(MTK_PLATFORM).o   # line 199
#   endif
#
# CONFIG_MTK_COMBO_CHIP is a Kconfig *string* symbol
# (drivers/misc/mediatek/connectivity/Kconfig:128, default "CONSYS_6761" if
# MTK_COMBO_CHIP_CONSYS_6761), and Kconfig writes string values WITH their
# quotes, so the module sees the 12-character word
#     "CONSYS_6761"
# and the quoted pattern matches.  This script used to override it on the make
# command line with a BARE `CONSYS_6761` -- and a command-line variable beats
# auto.conf -- so every one of those quoted tests failed and three things were
# switched off at once:
#     - common_main/platform/mt6761.o      the WMT IC-ops provider (line 199)
#     - -D MTK_WCN_SOC_CHIP_SUPPORT        (line 113, same quoted pattern)
#     - -D MTK_WCN_WLAN_GEN2               (line 139, same quoted pattern)
#
# Without mt6761.o the link keeps the __weak NULL stub from mtk_wcn_consys_hw.c
#     P_WMT_CONSYS_IC_OPS __weak mtk_wcn_get_consys_ic_ops(VOID)
#     { WMT_PLAT_PR_WARN("Does not support on combo\n"); return NULL; }
# so `wmt_consys_ic_ops` stays NULL and the first platform probe dereferences it:
#     mtk_wmt_probe() -> wmt_consys_ic_ops->consys_ic_need_store_pdev  (offset 0)
# -> kernel panic, ESR 96000005, x0=x1=x2=0 -- on the real device, as soon as the
# vendor userspace wmt_loader drives mtk_wcn_consys_hw_init().
#
# Evidence, by SYMBOL TABLE (port/elf_symbols.py).  Counting strings cannot tell
# the two builds apart: a module link is `ld -r`, so a __weak body survives as
# dead code and its log string stays in .rodata either way.
#     stock vendor 32-bit wmt_drv.ko : mtk_wcn_get_consys_ic_ops GLOBAL, 24 B
#                                      consys_ic_ops              GLOBAL, 172 B
#     ours, before this fix          : mtk_wcn_get_consys_ic_ops WEAK,   68 B
#                                      consys_ic_ops              ABSENT
#
# So derive the value from the kernel's own view instead of inventing it, and
# keep the quotes.  Both sources are consulted because auto.conf is the build's
# view while .config is what the defconfig actually wrote.
kcfg_value() {
  local f v
  for f in "$KOUT/include/config/auto.conf" "$KOUT/.config"; do
    [ -f "$f" ] || continue
    v=$(sed -n "s/^$1=//p" "$f" | tail -1)
    [ -n "$v" ] && { printf '%s' "$v"; return 0; }
  done
  return 1
}
# Strip one layer of surrounding double quotes -- the same idiom the kernel uses,
# e.g. arch/arm64/Makefile: `MTK_PLATFORM := $(CONFIG_MTK_PLATFORM:"%"=%)`.
unq() { local s="$1"; s="${s#\"}"; s="${s%\"}"; printf '%s' "$s"; }

COMBO_RAW=$(kcfg_value CONFIG_MTK_COMBO_CHIP || true)
COMBO_CHIP=$(unq "$COMBO_RAW")
if [ -z "$COMBO_CHIP" ]; then
  echo "WARN: CONFIG_MTK_COMBO_CHIP not found in $KOUT/.config or its auto.conf;"
  echo "      falling back to CONSYS_6761 (this device's only combo chip)"
  COMBO_CHIP=CONSYS_6761
fi
# Put the quotes back: common/Makefile's filter patterns include them, and a
# bare word silently disables the chip file again (the bug this block documents).
COMBO_CHIP_MAKE="\"$COMBO_CHIP\""

# MTK_PLATFORM normally arrives for free: arch/arm64/Makefile:138-140 does
#   MTK_PLATFORM := $(CONFIG_MTK_PLATFORM:"%"=%)
#   export MTK_PLATFORM ...
# and that Makefile is included even for an M= build (top-level Makefile:648 is
# outside the KBUILD_EXTMOD branches).  Re-assert it anyway so a change in that
# plumbing cannot silently re-open the hole -- an empty MTK_PLATFORM would turn
# line 199 into `common_main/platform/.o`, a hard Kbuild error.
MTK_PLATFORM_VALUE=$(unq "$(kcfg_value CONFIG_MTK_PLATFORM || true)")
[ -n "$MTK_PLATFORM_VALUE" ] || MTK_PLATFORM_VALUE=mt6761

echo "== common/Makefile variables =="
echo "   CONFIG_MTK_COMBO_CHIP = $COMBO_CHIP_MAKE   (quotes are REQUIRED)"
echo "   MTK_PLATFORM          = $MTK_PLATFORM_VALUE"
[ "$COMBO_CHIP" = "CONSYS_6761" ] || echo "   NOTE: combo chip is '$COMBO_CHIP', not CONSYS_6761 -- is the defconfig right?"

# ---- 4. Build each connectivity module -------------------------------------
# KBUILD_MODPOST_FAIL_ON_WARNINGS is forced 'y' by the WMT Makefile; some symbols
# live in other modules (loaded first at runtime) or in vmlinux, so relax it to
# avoid a hard build failure on legitimately-deferred symbol resolution.
build_mod() {
  local d="$1"; shift
  echo ">> build module: $CONN/$d"
  # Force-include the ARCHYTAS EMI/MPU compat header (created in step 2c) into
  # every translation unit. KCFLAGS is the most reliable vehicle because it is
  # folded into KBUILD_CFLAGS for all module compiles regardless of how the
  # module's -objs are laid out across subdirectories.
  #
  # -mcmodel=large: correct to pass, but NOT sufficient, and NOT the fix.
  #
  # The running kernel has CONFIG_ARM64_ERRATUM_843419=y (Cortex-A53 erratum),
  # which selects CONFIG_ARM64_MODULE_CMODEL_LARGE and makes
  # arch/arm64/kernel/module.c compile OUT the handlers for the ADRP
  # relocations R_AARCH64_ADR_PREL_PG_HI21/_NC (275/276):
  #     #ifndef CONFIG_ARM64_ERRATUM_843419
  #         case R_AARCH64_ADR_PREL_PG_HI21: ...
  # so any module still carrying one of those relocations is rejected by insmod:
  #     module wmt_drv: unsupported RELA relocation: 275   (then -ENOEXEC)
  #
  # arch/arm64/Makefile injects -mcmodel=large through KBUILD_CFLAGS_MODULE.
  # Reading DW_AT_producer back out of the built .ko proves it genuinely reaches
  # cc1 even for this out-of-tree (M=) build -- earlier claims that KCFLAGS or
  # ccflags-y were being dropped were WRONG. And it still does not help:
  # -mcmodel=large only changes how GCC addresses *symbols*; the 161 ADRP in
  # bt_drv.ko (197 in wmt_chrdev_wifi.ko) all target the .text SECTION symbol
  # with a small, 8-byte-aligned addend right after each function body -- i.e.
  # GCC's in-function literal pools, which the large memory model never removes.
  #
  # The actual fix is kernel-side and lives in step 2e:
  # port/patch_adrp_reloc.py removes the #ifndef guard so the loader relocates
  # ADRP again. We keep passing the flag explicitly because it is what the
  # Kconfig intends for modules, and report_relocs() prints the count so a
  # codegen regression cannot hide.
  #
  # Always rebuild: a cached/pre-existing .o would keep stale codegen and silently
  # defeat the -mcmodel=large change (that is exactly what fooled build #14/#15).
  #
  # HAZARD: this wipes the module's whole build directory, so 'wlan' also
  # destroys 'wlan/adaptor'. Callers must therefore build an ancestor BEFORE its
  # descendants and collect each .ko immediately (see collect_mod).
  rm -rf "$KOUT/$CONN/$d"
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       KCFLAGS="-mcmodel=large -Wno-error -include $WLAN_DIR/emi_compat.h" \
       AUTOCONF_H="$AUTOCONF" KERNEL_OUT="$KOUT" TOP="$KROOT" \
       KBUILD_MODPOST_FAIL_ON_WARNINGS= \
       M="$CONN/$d" "$@" modules -j"$JOBS"

  # Diagnostic: show the flags cc1 actually received, straight out of Kbuild's
  # own .<obj>.cmd command file. This is how we prove (or disprove) that
  # -mcmodel=large made it into the compile line.
  local _cmd _flags
  # `find ... | head -1` is a TRAP in this script: it runs under
  # `set -euo pipefail`, so when find keeps writing after head has exited it dies
  # on SIGPIPE (141) and pipefail turns that into a failing assignment, which
  # set -e then treats as fatal. For a module with many objects (common has
  # hundreds of .o.cmd files) that is guaranteed -- and it is precisely what
  # killed the run right after the first module, printing no [flags] line and no
  # other diagnostic. `-print -quit` stops find itself: no pipe, no SIGPIPE.
  _cmd=$(find "$KOUT/$CONN/$d" -name '*.o.cmd' -print -quit 2>/dev/null)
  if [ -n "$_cmd" ]; then
    _flags=$( { grep -o -E -- '-mcmodel=[a-z]+|-fno-PIE|-fPIE|-fpic|-fPIC|-include [^ ]*emi_compat.h' "$_cmd" || true; } \
              | sort -u | tr '\n' ' ')
    echo "   [flags] $(basename "$_cmd") => ${_flags:-<none of interest>}"
  else
    echo "   [flags] no .o.cmd under $KOUT/$CONN/$d (module may not have compiled)"
  fi
}

# Report the relocation profile of each shipped module. ADRP (275/276) is NOT
# fatal any more -- step 2e patches the kernel loader to relocate it -- but it is
# still worth printing, both to catch a codegen regression and because a count of
# zero would mean the modules no longer depend on the kernel patch at all.
# Also prints the codegen flags GCC recorded inside the .ko (DW_AT_producer), the
# only reliable way to prove which -m/-f flags actually reached cc1.
report_relocs() {
  local ko="$1"
  python3 - "$ko" <<'PY'
import re, struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
if d[:4] != b"\x7fELF" or d[4] != 2:
    sys.exit("not a 64-bit ELF: %s" % p)
(_t, _m, _v, _e, _po, shoff, _f,
 _eh, _pe, _pn, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    secs.append(struct.unpack_from("<IIQQQQIIQQ", d, o))
shstr_off = secs[shstrndx][4]
def nm(x):
    e = d.index(b"\0", shstr_off + x)
    return d[shstr_off + x:e].decode("utf-8", "replace")
adrp = 0
for s in secs:
    if s[1] not in (4, 9) or not nm(s[0]).startswith(".rel"):
        continue
    esz = s[9] or 24
    for k in range(s[5] // esz):
        r_info = struct.unpack_from("<Q", d, s[4] + k * esz + 8)[0]
        if (r_info & 0xFFFFFFFF) in (275, 276):
            adrp += 1
flags = []
m = re.search(rb"GNU C[0-9][^\x00]{10,400}", d)
if m:
    for f in (b"-mcmodel=large", b"-fno-pic", b"-fPIE", b"-fpic",
              b"-fstack-protector", b"-fno-jump-tables"):
        if f in m.group(0):
            flags.append(f.decode())
print("   %-22s ADRP(275/276) = %-5d  codegen: %s"
      % (p.rsplit("/", 1)[-1], adrp, " ".join(flags) or "<unreadable>"))
PY
}


# Where the four .ko are gathered. Defined BEFORE the builds because
# collect_mod() copies each module out the instant it has been linked.
OUTDIR="$KOUT/connectivity-modules"
# Refuse to wipe anything that is not a plain subdirectory of the build output.
case "$OUTDIR" in
  "$KOUT"/*) : ;;
  *) echo "ERROR: refusing to clean OUTDIR outside KOUT: $OUTDIR"; exit 1 ;;
esac
# Start clean: a stale .ko left behind by an earlier local run would otherwise
# satisfy the "all four present" assertion without having been rebuilt at all.
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

# Copy a module's freshly linked .ko out of its build directory, immediately.
#
# Why not one sweep at the very end: build_mod() wipes $KOUT/$CONN/<dir> to
# force a clean rebuild, and 'wlan' is an ANCESTOR of 'wlan/adaptor'. With the
# sweep only at the end, the final 'build_mod wlan' deleted the adaptor module
# that had just been linked -- that is run #37189514054: wmt_chrdev_wifi.ko was
# linked at 08:47:34 and gone by the 08:48:14 find, ending in
# "ERROR: missing connectivity modules: wmt_chrdev_wifi.ko" even though the log
# showed a successful LD [M] for it. Copying into OUTDIR, which lives outside
# $KOUT/$CONN, makes the collected set independent of build order.
collect_mod() {
  local d="$1" f n=0
  while IFS= read -r f; do
    cp -f "$f" "$OUTDIR/"
    echo "   collected $(basename "$f")"
    n=$((n + 1))
  done < <(find "$KOUT/$CONN/$d" -type f -name '*.ko' -print 2>/dev/null)
  [ "$n" -gt 0 ] || echo "   WARN: no .ko found under $KOUT/$CONN/$d"
}

# BUILD ORDER MATTERS: an ancestor directory must be built before its
# descendants, because build_mod() wipes the module's whole build directory.
# 'wlan' therefore comes BEFORE 'wlan/adaptor'; the reverse order is exactly
# what destroyed the adaptor module in run #37189514054.
build_mod common  CONFIG_MTK_COMBO_CHIP="$COMBO_CHIP_MAKE" \
                    MTK_PLATFORM="$MTK_PLATFORM_VALUE"
collect_mod common

# Gate #1: the chip file MUST have been compiled. This is the precise symptom of
# the quoted-pattern bug documented in step 3b, and catching it here names the
# cause instead of leaving a puzzling NULL dereference for the device to find.
# (`${PLAT}.o` is mt6761.o: the IC ops table + the strong
# mtk_wcn_get_consys_ic_ops that overrides the __weak stub.)
if [ -f "$KOUT/$CONN/common/common_main/platform/$MTK_PLATFORM_VALUE.o" ]; then
  echo "   OK: $MTK_PLATFORM_VALUE.o compiled (WMT IC-ops provider is in the link)"
else
  echo "ERROR: $KOUT/$CONN/common/common_main/platform/$MTK_PLATFORM_VALUE.o missing."
  echo "       common/Makefile:198-199 gate it on \$(filter \"CONSYS_%\",\$(CONFIG_MTK_COMBO_CHIP))"
  echo "       -- the value must keep its Kconfig quotes (see step 3b). Without this"
  echo "       object the module keeps the __weak mtk_wcn_get_consys_ic_ops stub and"
  echo "       mtk_wmt_probe() panics on a NULL wmt_consys_ic_ops."
  exit 1
fi

# MTK_ANDROID_WMT=y is REQUIRED here. This SoC uses the legacy WMT glue, not
# conninfra, so CFG_SUPPORT_CONNINFRA is forced to 0. But gl_rst.c/axi.c compile
# their WMT code whenever CFG_SUPPORT_CONNINFRA==0 (NOT additionally gated on
# CFG_MTK_ANDROID_WMT), while the header that declares those symbols is included
# only under `#if CFG_MTK_ANDROID_WMT && (CFG_SUPPORT_CONNINFRA == 0)` (gl_rst.h)
# -> without it: CFG_MTK_ANDROID_WMT==0, wmt_exp.h never included, yet
# gl_rst.c still references WMTDRV_TYPE_WIFI / mtk_wcn_wmt_msgcb_reg /
# mtk_wcn_wmt_assert_timeout / WMTMSG_TYPE_RESET / WMTRSTMSG_* -> undeclared.
# Setting MTK_ANDROID_WMT=y also makes the wlan Makefile emit
# -I.../common/common_main/include and .../common_main/linux/include, which is
# where wmt_exp.h (and its deps osal.h/wmt_plat.h/stp_exp.h) live.
#
# MTK_COMBO_CHIP=CONNAC (the chip FAMILY, not the chip part number) is what
# actually selects the Wi-Fi chip layer -- see Gate #2 just below.  WLAN_CHIP_ID is set
# explicitly, exactly as the vendor's own Android.mk does
# (wlan/Android.mk:14 passes `MTK_COMBO_CHIP=$(WIFI_CHIP) WLAN_CHIP_ID=$(WLAN_CHIP_ID)`),
# and it only feeds `wlan_<lower(WLAN_CHIP_ID)>_<HIF>` plus a few per-part
# tweaks (6765/6873/6779), so this keeps the module name wlan_mt6761_axi.ko.
build_mod wlan    MTK_COMBO_CHIP=CONNAC \
                    WLAN_CHIP_ID=MT6761 \
                    MTK_ANDROID_WMT=y \
                    CONFIG_MTK_COMBO_WIFI_HIF=axi \
                    CONFIG_MTK_COMBO_WIFI=m \
                    CONFIG_WLAN_DRV_BUILD_IN=n
collect_mod wlan

# Gate #2: the CONNAC chip layer MUST have been compiled. Without
# MTK_COMBO_CHIP=CONNAC the wlan Makefile simply never adds
# chips/connac/connac.o (Makefile:590-592) -- while cmm_asic_connac.o and
# dbg_connac.o are added UNCONDITIONALLY, so the module still looks like it has
# connac code in it. What is really missing is the chip data: connac.c provides
# mt66xx_driver_data_connac, which is `mtk_axi_ids[0].driver_data`. With it NULL,
# mtk_axi_probe+0x2c dereferences NULL on the very first probe and the device
# panics (observed: PC is at mtk_axi_probe+0x2c/0x48, x1 = 0).
if [ -f "$KOUT/$CONN/wlan/chips/connac/connac.o" ]; then
  echo "   OK: chips/connac/connac.o compiled (CONNAC chip layer present)"
else
  echo "ERROR: $KOUT/$CONN/wlan/chips/connac/connac.o missing."
  echo "       wlan/Makefile:590 gates it on \$(filter CONNAC,\$(MTK_COMBO_CHIP));"
  echo "       the chip FAMILY must be CONNAC even though the part is MT6761."
  echo "       Without it mtk_axi_ids[0].driver_data is NULL and mtk_axi_probe"
  echo "       panics on the first probe."
  exit 1
fi

build_mod wlan/adaptor CONFIG_MTK_COMBO_CHIP="$COMBO_CHIP_MAKE"
collect_mod wlan/adaptor

build_mod bt      CONFIG_MTK_COMBO_CHIP="$COMBO_CHIP_MAKE"
collect_mod bt

# ---- 5. Rename + verify the four modules -----------------------------------
# All four .ko were already copied into $OUTDIR by collect_mod(), the moment
# each one was linked. Nothing is swept up here any more: a late sweep is what
# silently lost a module (see collect_mod).
#
# The wlan-core module name is DERIVED by its own Makefile, NOT fixed:
#   MODULE_NAME := wlan_<lower(WLAN_CHIP_ID)>_<CONFIG_MTK_COMBO_WIFI_HIF>
#   WLAN_CHIP_ID = $(word 1, $(MTK_COMBO_CHIP))    # only when it is not passed
# We pass WLAN_CHIP_ID=MT6761 explicitly (the chip FAMILY lives in
# MTK_COMBO_CHIP=CONNAC, so the `word 1` fallback would yield "connac"), with
# HIF=axi -- so the real filename is
#   wlan_mt6761_axi.ko          (note the 'mt' prefix: lower('MT6761')='mt6761')
# The old hardcoded 'wlan_6761_axi.ko' never matched, so this step silently
# dropped the main Wi-Fi driver and the artifact shipped with only 3 of 4 .ko.
# The vendor init.rc insmods it by the hardcoded path .../wlan_drv_gen4m.ko, so
# rename it; the module's real identity stays in .modinfo, which the dump below
# prints. FAIL LOUDLY when it is missing.
echo "=== debug: every .ko produced under KOUT ==="
find "$KOUT" -type f -name '*.ko' 2>/dev/null | sort || true

# Glob, not `find | head -1`: this runs under `set -euo pipefail`, where a
# SIGPIPE on the pipeline would turn a successful match into a fatal error.
_w=""
for _cand in "$OUTDIR"/wlan_*.ko; do
  [ -f "$_cand" ] || continue
  case "$_cand" in *'/wlan_drv_gen4m.ko') continue ;; esac
  _w="$_cand"
  break
done
if [ -n "$_w" ]; then
  cp -f "$_w" "$OUTDIR/wlan_drv_gen4m.ko"
  echo "   wlan core module: $(basename "$_w") -> wlan_drv_gen4m.ko"
  # Drop the pre-rename copy. collect_mod() names it wlan_mt6761_axi.ko, and
  # without this the artifact glob *.ko would ship the SAME ~55 MiB module twice.
  rm -f "$_w"
else
  echo "ERROR: wlan core module (wlan_*.ko) not found in $OUTDIR;"
  echo "       expected wlan_mt6761_axi.ko -- see the build log above"
  exit 1
fi

# Relocation profile of every module.
#
# Deliberately read BEFORE the strip below: report_relocs() recovers the codegen
# flags (-mcmodel=large, -fno-pic, ...) from the DW_AT_producer string, which
# only exists in .debug_info. `strip -g` does not touch .rela.*, so the ADRP
# counts printed here still describe the file we go on to ship.
echo "=== relocation profile ==="
for m in "$OUTDIR"/*.ko; do
  report_relocs "$m"
done

# ---- 5b. Strip debug info --------------------------------------------------
# Same as INSTALL_MOD_STRIP=1 (Kbuild uses --strip-debug), and the vendor's own
# modules on the device are stripped: measured with port/elf_debug_share.py, all
# seven stock modules report `strip -g removes 0 bytes`.
#
# The debug data is 43-50% of our modules (the defconfig keeps -g):
#   bt_drv.ko          461,496 -> 240,193
#   wmt_chrdev_wifi.ko 686,368 -> 342,804
#   wmt_drv.ko       8,879,904 -> 5,072,216
#   wlan core       57,129,152 -> ~29,000,000
#
# Note what this does NOT change: SHF_ALLOC (what insmod actually maps into
# module memory) is ~21 KiB / ~17 KiB / ~1.08 MiB and is comparable to the stock
# 32-bit modules (19 KiB / 13 KiB / 941 KiB). So the 57 MiB is a shipping and
# adb-push cost, never a reason insmod could fail.
#
# `-g` (--strip-debug) removes ONLY debug sections; .symtab, .modinfo,
# __versions and .rela.* all survive, which the modinfo dump below re-verifies
# on the stripped files. A strip that damaged a module therefore fails the build
# here instead of reaching the device.
STRIP="${CROSS_COMPILE}strip"
if command -v "$STRIP" >/dev/null 2>&1; then
  echo "=== strip -g (debug info only, as INSTALL_MOD_STRIP=1 does) ==="
  _tot_a=0; _tot_b=0
  for _m in "$OUTDIR"/*.ko; do
    _a=$(stat -c %s "$_m" 2>/dev/null || wc -c < "$_m")
    "$STRIP" -g "$_m" || { echo "ERROR: strip failed on $_m"; exit 1; }
    _b=$(stat -c %s "$_m" 2>/dev/null || wc -c < "$_m")
    _tot_a=$((_tot_a + _a)); _tot_b=$((_tot_b + _b))
    echo "   $(basename "$_m"): $_a -> $_b bytes"
    # A strip that silently did nothing would leave the artifact bloated while
    # every other check still passed -- so say so loudly.
    [ "$_b" -lt "$_a" ] || echo "   WARN: strip did not shrink $(basename "$_m")"
  done
  echo "   total: $_tot_a -> $_tot_b bytes"
else
  echo "NOTE: $STRIP not found -- shipping UNSTRIPPED modules (much larger .ko);"
  echo "      install binutils-aarch64-linux-gnu to shrink the artifact"
fi

echo "=== built (shipped) connectivity modules ==="
ls -l "$OUTDIR"/*.ko 2>/dev/null || { echo "ERROR: no modules produced"; exit 1; }

# Hard assertion: the artifact must contain all four modules. Without this the
# CI could go green while silently shipping an incomplete set (the exact bug
# that produced build #8 with only wmt_drv/wmt_chrdev_wifi/bt_drv).
_missing=""
for _want in wmt_drv wmt_chrdev_wifi bt_drv wlan_drv_gen4m; do
  [ -f "$OUTDIR/$_want.ko" ] || _missing="$_missing $_want.ko"
done
if [ -n "$_missing" ]; then
  echo "ERROR: missing connectivity modules:$_missing"
  exit 1
fi
echo "   OK: all 4 connectivity modules present."

# Gate #3: the WMT IC-ops provider must be a STRONG symbol in the SHIPPED bytes.
# A surviving __weak stub is not a cosmetic warning -- it IS the NULL-deref panic
# (see step 3b), so the build fails here rather than on the device.
#
# Read AFTER the strip: `-g` (--strip-debug) keeps .symtab, so what is inspected
# is exactly what gets pushed to /data/local/tmp and insmod'ed.
echo "=== IC-ops linkage gate (must be GLOBAL, not WEAK) ==="
_sym=$(python3 "$PORT/elf_symbols.py" "$OUTDIR/wmt_drv.ko" \
       --grep '^mtk_wcn_get_consys_ic_ops$|^consys_ic_ops$' 2>&1) || true
printf '%s\n' "$_sym"
if printf '%s\n' "$_sym" | grep -qE '^  mtk_wcn_get_consys_ic_ops +GLOBAL +FUNC' \
   && printf '%s\n' "$_sym" | grep -qE '^  consys_ic_ops +GLOBAL +OBJECT'; then
  echo "   OK: strong mtk_wcn_get_consys_ic_ops + consys_ic_ops are linked in."
  echo "       -> mtk_wmt_probe() can no longer dereference a NULL wmt_consys_ic_ops."
else
  echo "ERROR: wmt_drv.ko still carries the __weak NULL stub instead of the"
  echo "       mt6761 IC ops. Loading it would panic the device on the first"
  echo "       platform probe (mtk_wmt_probe -> NULL wmt_consys_ic_ops)."
  echo "       Re-check step 3b: CONFIG_MTK_COMBO_CHIP must be quoted, and"
  echo "       common_main/platform/$MTK_PLATFORM_VALUE.o must be in the link."
  exit 1
fi

# Gate #4: the wlan core must still carry its chip driver data.
#
# mtk_axi_probe() reads its chip description from the platform_device_id table:
#     prDriverData = (struct mt66xx_hif_driver_data *) mtk_axi_ids[0].driver_data;
#     prChipInfo   = prDriverData->chip_info;
# and `mtk_axi_ids[0].driver_data` is compiled in ONLY under #ifdef CONNAC /
# CONNAC2X2 / SOC3_0 (axi.c:101-114).  If the chip family is not selected the
# entry stays zero, prDriverData is NULL, and the very first probe faults:
#     PC is at mtk_axi_probe+0x2c/0x48 [wlan_mt6761_axi]   x1 = 0  -> panic
# So require the selected chip's driver data to be a real GLOBAL OBJECT in the
# shipped bytes.  chips/connac/connac.o (Gate #2) is the mechanism; this is the
# observable outcome, and it is what actually keeps the device alive.
echo "=== wlan chip driver-data gate (mtk_axi_ids[] must be non-NULL) ==="
_wsym=$(python3 "$PORT/elf_symbols.py" "$OUTDIR/wlan_drv_gen4m.ko" \
        --grep '^mt66xx_driver_data_' 2>&1) || true
printf '%s\n' "$_wsym"
if printf '%s\n' "$_wsym" | grep -qE '^  mt66xx_driver_data_[A-Za-z0-9_]+ +GLOBAL +OBJECT'; then
  echo "   OK: chip driver data present -> mtk_axi_ids[0].driver_data is non-NULL."
else
  echo "ERROR: wlan_drv_gen4m.ko defines NO mt66xx_driver_data_* object."
  echo "       mtk_axi_ids[0].driver_data is therefore 0 and mtk_axi_probe"
  echo "       will dereference NULL and panic the device. Re-check the wlan"
  echo "       build_mod invocation above: MTK_COMBO_CHIP must carry the chip"
  echo "       FAMILY (CONNAC for MT6761), not the part number."
  exit 1
fi

# Gate #5: EMI section download must be real, not stubbed out.
# When CFG_MTK_ANDROID_EMI=0 (old broken build), wlanDownloadEMISection() in
# fw_dl.c compiled to an 8-byte MOV+RET stub that silently returned success
# without writing anything to the EMI region.  Firmware sections whose tailers
# had DOWNLOAD_CONFIG_EMI set were never downloaded -> firmware ran with
# uninitialized EMI memory -> stp_trace32_dump type=5 exception at
# EVA=0xF0400000 during FW_START.
#
# With CFG_MTK_ANDROID_EMI=1 (this fix) the function does request_mem_region +
# ioremap_nocache + kalMemCopy + release_mem_region -- its size grows from 8 to
# ~100 bytes.  We gate on that threshold, AND that the EMI MPU stubs from
# compat_stub.c ARE referenced by the module (proof the EMI code path is
# actually taken).
echo "=== EMI download gate (CFG_MTK_ANDROID_EMI=1 must be effective) ==="
_esym=$(python3 "$PORT/elf_symbols.py" "$OUTDIR/wlan_drv_gen4m.ko" \
       --grep '^wlanDownloadEMISection$|^kalSetEmiMpuProtection$' 2>&1) || true
printf '%s\n' "$_esym"
_emi_ok=true
if printf '%s\n' "$_esym" | grep -qE '^  wlanDownloadEMISection +[A-Z]+ +FUNC'; then
  _emi_fn=$(printf '%s\n' "$_esym" | grep -E '^  wlanDownloadEMISection +[A-Z]+ +FUNC')
  # last field is the size -- e.g. "wlanDownloadEMISection LOCAL FUNC sz=108"
  _sz=$(echo "$_emi_fn" | grep -oE 'sz=[0-9]+$' | cut -d= -f2)
  echo "   wlanDownloadEMISection size = ${_sz:-?}"
  if [ "${_sz:-0}" -le 16 ]; then
    echo "ERROR: wlanDownloadEMISection is still a stub (size <= 16)."
    echo "       CFG_MTK_ANDROID_EMI is not being set to 1. EMI firmware"
    echo "       sections would be silently skipped again, crashing the"
    echo "       firmware at EVA=0xF0400000 during FW_START."
    _emi_ok=false
  else
    echo "   OK: wlanDownloadEMISection is real (size > 16)."
  fi
else
  echo "ERROR: wlanDownloadEMISection symbol not found at all."
  _emi_ok=false
fi
if printf '%s\n' "$_esym" | grep -qE '^  kalSetEmiMpuProtection +[A-Z]+ +FUNC'; then
  echo "   OK: kalSetEmiMpuProtection is linked (EMI MPU path reachable)."
else
  echo "ERROR: kalSetEmiMpuProtection is missing."
  _emi_ok=false
fi
[ "$_emi_ok" = true ] || { echo "   GATE FAIL"; exit 1; }

# Hard gate for the ONLY thing that makes those modules loadable: the target
# kernel must carry the ADRP relocation fix from step 2e. Shipping .ko files that
# need a kernel without the fix would just reproduce the insmod -ENOEXEC failure.
if grep -q "ARCHYTAS_ADRP_RELOC" "$KROOT/arch/arm64/kernel/module.c" 2>/dev/null; then
  echo "   OK: kernel source carries the ADRP relocation fix (ARCHYTAS_ADRP_RELOC)."
  echo "       -> the boot image built from THIS tree must be flashed once."
else
  echo "ERROR: arch/arm64/kernel/module.c does NOT carry the ADRP relocation fix;"
  echo "       any module containing ADRP would be rejected by insmod (-ENOEXEC)."
  exit 1
fi

# kernel -> module. `modinfo` on the runner would describe the RUNNER's kernel,
# not the target, so read the strings straight out of the ELF `.modinfo` section.
# This matters: a vermagic mismatch makes insmod fail instantly
# ("version magic ... should be ..."), and the module's internal `name=` is what
# lsmod will show (the on-disk filename wlan_drv_gen4m.ko is just what the
# vendor init.rc insmods by path).
#
# Also counts __versions entries, because the target vermagic contains
# "modversions" (CONFIG_MODVERSIONS=y). Symbol CRCs are pulled from the kernel's
# Module.symvers at modpost time; a --modules-only run never builds vmlinux, so
# that file may be absent and __versions comes out empty. That is survivable --
# check_version() only rejects a symbol whose name IS listed with a different
# crc, and an empty (but present) __versions falls through to its
# "broken toolchain, warn once, return 1" path -- but it is worth seeing, since a
# MISSING section would instead fail with -ENOEXEC.
echo "=== modinfo (name / vermagic / __versions) ==="
if [ -f "$KOUT/Module.symvers" ]; then
  echo "   kernel Module.symvers present: $(wc -l < "$KOUT/Module.symvers") symbols"
else
  echo "   NOTE: $KOUT/Module.symvers absent (expected in --modules-only mode;"
  echo "         symbol CRCs will be empty -> __versions entry count 0)"
fi
for m in "$OUTDIR"/*.ko; do
  python3 - "$m" <<'PY'
import struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
(_t, _m, _v, _e, _po, shoff, _f,
 _eh, _pe, _pn, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    secs.append(struct.unpack_from("<IIQQQQIIQQ", d, o))
shstr_off = secs[shstrndx][4]
def nm(x):
    e = d.index(b"\0", shstr_off + x)
    return d[shstr_off + x:e].decode("utf-8", "replace")
info = {}
ver_size = None
for s in secs:
    name = nm(s[0])
    if name == ".modinfo":
        for entry in d[s[4]:s[4] + s[5]].split(b"\0"):
            if b"=" in entry:
                k, _, v = entry.decode("utf-8", "replace").partition("=")
                info[k] = v
    elif name == "__versions":
        ver_size = s[5]
print("   %-22s name=%-20s __versions=%-4s vermagic=%s"
      % (p.rsplit("/", 1)[-1], info.get("name", "?"),
         "absent" if ver_size is None else "%d" % (ver_size // 72),
         info.get("vermagic", "<missing!>")))
PY
done
