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
#   vendor/mediatek/kernel_modules/connectivity/wlan        -> wlan_6761_axi.ko
#   vendor/mediatek/kernel_modules/connectivity/wlan/adaptor-> wmt_chrdev_wifi.ko
#   vendor/mediatek/kernel_modules/connectivity/bt          -> bt_drv.ko
#
# Usage: bash port/build_connectivity_modules.sh <KERNEL_SRC> <KERNEL_OUT>
#
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Normalize KROOT/KOUT to ABSOLUTE paths. Kbuild resolves O= relative to the
# directory entered via 'make -C $KROOT', so a relative O= would be re-based as
# $KROOT/$KOUT (e.g. kernel/kernel/out-archytas) and the prebuilt .config there
# would be missing -> "Configuration file .config not found" at modules_prepare.
abspath() { ( cd "$(dirname "$1")" 2>/dev/null && echo "$(pwd)/$(basename "$1")" ) || echo "$1"; }
KROOT="$(abspath "${1:-$ROOT/kernel}")"
KOUT="$(abspath "${2:-$ROOT/kernel/out-archytas}")"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"

CONN="vendor/mediatek/kernel_modules/connectivity"
AUTOCONF="$KOUT/include/generated/autoconf.h"

echo "== build_connectivity_modules =="
echo "KROOT = $KROOT"
echo "KOUT  = $KOUT"
echo "CROSS = $CROSS_COMPILE"
test -f "$AUTOCONF" || { echo "ERROR: $AUTOCONF missing; build the kernel (modules_prepare) first"; exit 1; }
command -v git  >/dev/null 2>&1 || { echo "ERROR: git required"; exit 1; }
command -v patch >/dev/null 2>&1 || echo "WARN: patch not found; relying on git apply only"

# ---- 1. Fetch the four pinned external repos -------------------------------
fetch_repo() {
  local url="$1" commit="$2" dest="$3"
  echo ">> fetch $url @ $commit"
  echo "   -> $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  if ! git clone --depth 1 "$url" "$dest" >/dev/null 2>&1; then
    echo "ERROR: clone failed for $url"; exit 1
  fi
  # Pin to the exact commit. GitHub allows fetching a bare SHA; try a shallow
  # fetch first, then a plain fetch, then (last resort) unshallow the clone so
  # the pinned object is actually reachable before checkout.
  _pinned=0
  if git -C "$dest" fetch --depth 1 origin "$commit" >/dev/null 2>&1; then
    _pinned=1
  elif git -C "$dest" fetch origin "$commit" >/dev/null 2>&1; then
    _pinned=1
  else
    git -C "$dest" fetch --unshallow origin >/dev/null 2>&1 || true
    if git -C "$dest" fetch origin "$commit" >/dev/null 2>&1; then
      _pinned=1
    fi
  fi
  if [ "$_pinned" = 1 ] && git -C "$dest" checkout -q "$commit" 2>/dev/null; then
    echo "   pinned to $commit"
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

# ---- 3. Prepare generated headers / vmlinux symtab for out-of-tree modpost
echo ">> modules_prepare"
make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
     HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
     KCFLAGS="-Wno-error" modules_prepare -j"$JOBS"

# ---- 4. Build each connectivity module -------------------------------------
# KBUILD_MODPOST_FAIL_ON_WARNINGS is forced 'y' by the WMT Makefile; some symbols
# live in other modules (loaded first at runtime) or in vmlinux, so relax it to
# avoid a hard build failure on legitimately-deferred symbol resolution.
build_mod() {
  local d="$1"; shift
  echo ">> build module: $CONN/$d"
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       KCFLAGS="-Wno-error" \
       AUTOCONF_H="$AUTOCONF" KERNEL_OUT="$KOUT" TOP="$KROOT" \
       KBUILD_MODPOST_FAIL_ON_WARNINGS= \
       M="$CONN/$d" "$@" modules -j"$JOBS"
}

build_mod common  CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod wlan/adaptor CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod bt      CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod wlan    MTK_COMBO_CHIP=MT6761 \
                    CONFIG_MTK_COMBO_WIFI_HIF=axi \
                    CONFIG_MTK_COMBO_WIFI=m \
                    CONFIG_WLAN_DRV_BUILD_IN=n

# ---- 5. Collect the four modules -------------------------------------------
OUTDIR="$KOUT/connectivity-modules"
mkdir -p "$OUTDIR"
find "$KOUT" "$KROOT/$CONN" -name 'wmt_drv.ko'        -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'wmt_chrdev_wifi.ko' -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'bt_drv.ko'         -exec cp -f {} "$OUTDIR/" \;
_w=$(find "$KOUT" "$KROOT/$CONN" -name 'wlan_6761_axi.ko' | head -1)
if [ -n "$_w" ]; then cp -f "$_w" "$OUTDIR/wlan_drv_gen4m.ko"; fi

echo "=== built connectivity modules ==="
ls -l "$OUTDIR"/*.ko 2>/dev/null || { echo "ERROR: no modules produced"; exit 1; }

# vermagic sanity check
echo "=== vermagic ==="
for m in "$OUTDIR"/*.ko; do
  echo "$(basename "$m"): $(modinfo -k "$(uname -r)" "$m" 2>/dev/null | grep -i vermagic || true)"
done
