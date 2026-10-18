#!/usr/bin/env bash
# =============================================================================
# build_twrp64.sh — 为小米小爱老师 Wi-Fi 版(Archytas, MT6761)组装 64 位 TWRP
#
# 背景:现役 TWRP 3.7.0_9-0 是 32 位用户态,刷 BiTGApps arm64 zip 时
#   /tmp/busybox-arm: not executable: 64-bit ELF file 失败。
# 本脚本用 Actions 构建的 64 位内核(boot-archytas-wifi.img,Image.gz-dtb 自带
# Wi-Fi dtb)+ 小米 Play(lotus, 同 MT6761)的 arm64 TWRP ramdisk 组包:
#   kernel   = boot-archytas-wifi.img 的 kernel 段(64 位 ARM64,已含 Wi-Fi DTB)
#   ramdisk  = lotus arm64 TWRP ramdisk,覆盖 Archytas /etc/recovery.fstab
# 用法:
#   ./build_twrp64.sh <boot-archytas-wifi.img> <lotus_twrp.img> <archytas_recovery.img>
# 依赖:mkbootimg/unpackbootimg(https://github.com/osm0sis/mkbootimg,make 编译)
#       python3(ramdisk cpio 打包,本目录 mk_ramdisk.py)
# =============================================================================
set -euo pipefail

BOOT64="${1:?用法: build_twrp64.sh <boot-archytas-wifi.img> <lotus_twrp.img> <archytas_recovery.img>}"
LOTUS="${2:?缺 lotus_twrp.img(小米 Play arm64 TWRP)}"
ARCHYTAS_RECOVERY="${3:?缺 archytas_recovery.img(现役 32 位 TWRP,取 recovery.fstab)}"

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d /tmp/twrp64.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
echo "==> 工作目录: $WORK"

MKBOOTIMG="$(command -v mkbootimg || echo "$HERE/../../../../twrp64-work/mkbootimg/mkbootimg")"
UNPACK="$(command -v unpackbootimg || echo "$HERE/../../../../twrp64-work/mkbootimg/unpackbootimg")"
[[ -x "$MKBOOTIMG" && -x "$UNPACK" ]] || { echo "!! 需要 mkbootimg/unpackbootimg(osm0sis, make 编译)"; exit 1; }

# 1) 从 64 位内核 boot 镜像取 kernel 段 + 打包字段
echo "==> 解包 64 位内核 boot"
mkdir -p "$WORK/boot64"; "$UNPACK" -i "$BOOT64" -o "$WORK/boot64" >/dev/null
KERNEL="$WORK/boot64/$(basename "$BOOT64")-kernel"
BASE="$(cat "$WORK/boot64/$(basename "$BOOT64")-base")"
CMD="$(cat "$WORK/boot64/$(basename "$BOOT64")-cmdline")"
KOFF="$(cat "$WORK/boot64/$(basename "$BOOT64")-kernel_offset")"
ROFF="$(cat "$WORK/boot64/$(basename "$BOOT64")-ramdisk_offset")"
TOFF="$(cat "$WORK/boot64/$(basename "$BOOT64")-tags_offset")"
SOFF="$(cat "$WORK/boot64/$(basename "$BOOT64")-second_offset")"
OSV="$(cat "$WORK/boot64/$(basename "$BOOT64")-os_version")"
OSPL="$(cat "$WORK/boot64/$(basename "$BOOT64")-os_patch_level")"
echo "   base=$BASE cmdline=$CMD"

# 2) lotus arm64 ramdisk 解包 → 覆盖 Archytas fstab → 清理 MIUI 残留
echo "==> 准备 arm64 ramdisk"
mkdir -p "$WORK/lotus_img"; "$UNPACK" -i "$LOTUS" -o "$WORK/lotus_img" >/dev/null
gzip -dc "$WORK/lotus_img/$(basename "$LOTUS")-ramdisk" > "$WORK/lotus.cpio"
mkdir -p "$WORK/lotus"
python3 - "$WORK/lotus.cpio" "$WORK/lotus" << 'PYEOF'
import os, sys
data=open(sys.argv[1],'rb').read(); out=sys.argv[2]
pos=0
while pos+110<=len(data):
    if data[pos:pos+6]!=b'070701': break
    hdr=data[pos:pos+110]
    namesz=int(hdr[94:102],16); size=int(hdr[54:62],16); mode=int(hdr[14:22],16)
    name=data[pos+110:pos+110+namesz-1].decode()
    body=((pos+110+namesz+3)&~3)
    if name=='TRAILER!!!': break
    rel=name.rstrip('/'); dest=os.path.join(out,rel)
    if mode&0o170000==0o040000: os.makedirs(dest,exist_ok=True)
    else:
        os.makedirs(os.path.dirname(dest),exist_ok=True)
        open(dest,'wb').write(data[body:body+size])
    pos=((body+size+3)&~3)
print(f"   lotus ramdisk 解包完成")
PYEOF

# 3) 覆盖 Archytas fstab、删除 MIUI 特有文件
mkdir -p "$WORK/archytas"; "$UNPACK" -i "$ARCHYTAS_RECOVERY" -o "$WORK/archytas" >/dev/null
mkdir -p "$WORK/archytas_rd"
python3 - "$WORK/archytas/$(basename "$ARCHYTAS_RECOVERY")-ramdisk" "$WORK/archytas_rd" << 'PYEOF'
import os, sys, gzip
data=gzip.open(sys.argv[1],'rb').read(); out=sys.argv[2]
pos=0
while pos+110<=len(data):
    if data[pos:pos+6]!=b'070701': break
    hdr=data[pos:pos+110]
    namesz=int(hdr[94:102],16); size=int(hdr[54:62],16); mode=int(hdr[14:22],16)
    name=data[pos+110:pos+110+namesz-1].decode()
    body=((pos+110+namesz+3)&~3)
    if name=='TRAILER!!!': break
    rel=name.rstrip('/'); dest=os.path.join(out,rel)
    if mode&0o170000==0o040000: os.makedirs(dest,exist_ok=True)
    else:
        os.makedirs(os.path.dirname(dest),exist_ok=True)
        open(dest,'wb').write(data[body:body+size])
    pos=((body+size+3)&~3)
print(f"   Archytas ramdisk 解包完成")
PYEOF
cp "$WORK/archytas_rd/etc/recovery.fstab" "$WORK/lotus/etc/recovery.fstab"
rm -f "$WORK/lotus/miui.factoryreset.fstab" "$WORK/lotus/miui.factoryreset.rc"
echo "   fstab 已覆盖,MIUI 残留已删"

# 4) 重打包 ramdisk(cpio newc → gzip)
echo "==> 重打包 arm64 ramdisk"
python3 "$HERE/mk_ramdisk.py" "$WORK/lotus" "$WORK/ramdisk.cpio"
gzip -9 -n -c "$WORK/ramdisk.cpio" > "$WORK/ramdisk.img"

# 5) mkbootimg 组装
echo "==> 组装 recovery-archytas-arm64.img"
"$MKBOOTIMG" \
  --kernel "$KERNEL" --ramdisk "$WORK/ramdisk.img" \
  --base "$BASE" --kernel_offset "$KOFF" --ramdisk_offset "$ROFF" \
  --tags_offset "$TOFF" --second_offset "$SOFF" \
  --pagesize 2048 --header_version 1 \
  --cmdline "$CMD" --os_version "$OSV" --os_patch_level "$OSPL" \
  --hashtype sha1 \
  --output recovery-archytas-arm64.img
echo "==> DONE: $(pwd)/recovery-archytas-arm64.img"
file recovery-archytas-arm64.img
