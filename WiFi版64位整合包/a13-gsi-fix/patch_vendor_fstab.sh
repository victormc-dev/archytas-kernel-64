#!/bin/bash
# ============================================================
# patch_vendor_fstab.sh — 修小爱 vendor 的 /data 挂载方式(FDE → FBE)
#
# 背景:小爱原厂 vendor 的 fstab.mt6761 里 userdata 行是 Android 9 时代的
#       FDE 写法 `encryptable=/dev/block/.../metadata`;Android 11+ 的
#       init/fs_mgr 已移除 FDE 支持,刷 A11/A12/A13 GSI 后 /data 挂不上,
#       zygote 起不来(现象:开机卡死、adb 全程无影子)。
#       需改为 A13 支持的 FBE 写法 `fileencryption=aes-256-xts:aes-256-cts`
#       (本机有 keymaster@3.0,FBE 可正常初始化)。
#
# 用法:
#   ./patch_vendor_fstab.sh <vendor.img> [输出名]
# 依赖:debugfs + e2fsck (e2fsprogs)
# ============================================================
set -e
SRC="${1:?用法: $0 <vendor.img> [输出名]}"
OUT="${2:-${SRC%.img}_fbe.img}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[1/4] 提取 fstab.mt6761 ..."
debugfs -R "dump /etc/fstab.mt6761 $TMP/fstab.mt6761" "$SRC" >/dev/null 2>&1 \
  || { echo "提取失败:镜像路径或文件是否存在?"; exit 1; }

echo "[2/4] 替换 userdata 行(FDE → FBE)..."
python3 - "$TMP/fstab.mt6761" << 'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8', errors='replace').read()
old = 'encryptable=/dev/block/platform/bootdevice/by-name/metadata,'
new = 'fileencryption=aes-256-xts:aes-256-cts'
if old not in s:
    print('未找到 FDE 写法(可能已改过?)→ 退出')
    sys.exit(0)
s2 = s.replace(old, new)
open(p + '.new', 'w', encoding='utf-8').write(s2)
for line in s2.splitlines():
    if '/data ' in line:
        print('修改后:', line)
PYEOF
[ -f "$TMP/fstab.mt6761.new" ] || { echo "无需修改,直接复制原镜像"; cp "$SRC" "$OUT"; exit 0; }

echo "[3/4] 写回镜像 ..."
cp "$SRC" "$OUT"
debugfs -w -R "rm /etc/fstab.mt6761" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $TMP/fstab.mt6761.new /etc/fstab.mt6761" "$OUT" >/dev/null 2>&1 \
  || { echo "写回失败"; exit 1; }

echo "[4/4] 修复位图计数并校验 ..."
# debugfs 改完 superblock 空闲计数不同步,用 e2fsck 修一次
e2fsck -f -y "$OUT" >/dev/null 2>&1 || true
debugfs -R "cat /etc/fstab.mt6761" "$OUT" 2>/dev/null | grep "/data " || true
echo "完成: $OUT"
echo "刷机: fastboot flash vendor $OUT"
