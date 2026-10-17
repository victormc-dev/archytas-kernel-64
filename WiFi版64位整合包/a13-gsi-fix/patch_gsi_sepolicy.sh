#!/bin/bash
# ============================================================
# patch_gsi_sepolicy.sh — 给小爱老师(A13/A12/A11 GSI)打 sepolicy 补丁
# 作用:恢复 surfaceflinger 权限(刷 GSI 后丢失),修复
#       "output buffer not gpu writeable" 崩溃循环
# 用法:
#   ./patch_gsi_sepolicy.sh <system.img> [输出镜像名]
# 依赖:debugfs (e2fsprogs)
# 平台:Linux(本机)/ WSL。Windows 纯环境请用 7-Zip + python 替代,见 README
# ============================================================
set -e
SRC="${1:?用法: $0 <system.img> [输出名]}"
OUT="${2:-${SRC%.img}_sfpermissive.img}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[1/4] 提取 plat_sepolicy.cil ..."
debugfs -R "dump /system/etc/selinux/plat_sepolicy.cil $TMP/plat_sepolicy.cil" "$SRC" >/dev/null 2>&1 \
  || { echo "提取失败:检查镜像路径与文件存在性"; exit 1; }
grep -q "surfaceflinger" "$TMP/plat_sepolicy.cil" || { echo "警告:cil 里没有 surfaceflinger 域?"; }

echo "[2/4] 注入补丁 ..."
# 幂等:已有则跳过
if grep -q "^(\s*typepermissive\s\+surfaceflinger" "$TMP/plat_sepolicy.cil"; then
    echo "已包含 typepermissive surfaceflinger,跳过注入"
else
    printf '\n(typepermissive surfaceflinger)\n' >> "$TMP/plat_sepolicy.cil"
fi
wc -c "$TMP/plat_sepolicy.cil"

echo "[3/4] 写回镜像 ..."
cp "$SRC" "$OUT"
debugfs -w -R "rm /system/etc/selinux/plat_sepolicy.cil" "$OUT" >/dev/null 2>&1 || true
# 保留 inode:用 write 覆盖(debugfs write 会替换原文件内容,保 inode)
debugfs -w -R "write $TMP/plat_sepolicy.cil /system/etc/selinux/plat_sepolicy.cil" "$OUT" >/dev/null 2>&1 \
  || { echo "写回失败(可能需要先 rm 再 write)"; exit 1; }

echo "[4/4] 校验 ..."
debugfs -R "cat /system/etc/selinux/plat_sepolicy.cil" "$OUT" 2>/dev/null | tail -3
echo "完成: $OUT"
ls -l "$OUT"
echo "刷机: fastboot flash system $OUT"
