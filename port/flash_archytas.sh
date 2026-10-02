#!/usr/bin/env bash
# ============================================================================
# flash_archytas.sh —— 小爱老师 Wi-Fi 版（Archytas）内核刷入脚本
# 运行环境：Linux 构建机（需 mtk/MTKClient、设备已进下载模式）
# 安全策略：刷前【强制备份】原 boot/dtbo，写操作前【二次确认】。
# ============================================================================
set -euo pipefail

# ---- 1. 路径配置（按你实际位置改这里）-------------------------------------
# archimedes 移植产物（boot 镜像）。Stage1 用 build_archimedes.sh 产物，
# Stage2 用 build_archytas_wifi.sh 产物，二选一填下面这个变量即可。
BOOT_IMG="${BOOT_IMG:-/path/to/boot-archytas-wifi.img}"        # ← 改成你的移植 boot 镜像
# Wi-Fi 原厂 dtbo（【必须】是 Wi-Fi 原厂，不要 4G 的）
WIFI_DTBO="${WIFI_DTBO:-/path/to/柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/dtbo.bin}"
# 备份存放目录
BAK_DIR="${BAK_DIR:-./bak_$(date +%Y%m%d_%H%M%S)}"

echo "==> 检查 mtk 是否可用..."
command -v mtk >/dev/null 2>&1 || { echo "✗ 未找到 mtk，先装 MTKClient: pipx install mtkclient"; exit 1; }

echo "==> 进入 payload（如 ROM 的 txt 注明密码 123456，按需加 --auth 或交互输入）..."
mtk payload || { echo "✗ payload 失败：设备是否在下载模式？lsusb 看 0e8d:0003/7000"; exit 1; }

echo "==> 确认分区名（正常应有 boot / dtbo / vbmeta）..."
mtk partition getboot

# ---- 2. 强制备份（含 vbmeta）---------------------------------------------
mkdir -p "$BAK_DIR"
echo "==> 强制备份原 boot / dtbo / vbmeta 到 $BAK_DIR ..."
mtk r boot   "$BAK_DIR/boot_bak.img"
mtk r dtbo   "$BAK_DIR/dtbo_bak.img"
mtk r vbmeta "$BAK_DIR/vbmeta_bak.img"
echo "✓ 备份完成。回退时用："
echo "    mtk w boot $BAK_DIR/boot_bak.img"
echo "    mtk w dtbo $BAK_DIR/dtbo_bak.img"
echo "    mtk w vbmeta $BAK_DIR/vbmeta_bak.img"

# ---- 3. 二次确认 -----------------------------------------------------------
echo ""
echo "即将写入："
echo "  boot  -> $BOOT_IMG"
echo "  dtbo  -> $WIFI_DTBO   (Wi-Fi 原厂，勿换 4G)"
echo "  vbmeta-> --disable-verification  (archimedes boot 无 AVB 哈希，需禁用验证)"
echo ""
read -r -p "确认刷入? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消，未写入。"; exit 0; }

# ---- 4. 写入 ---------------------------------------------------------------
echo "==> 刷 boot ..."
mtk w boot "$BOOT_IMG"
echo "==> 刷 dtbo（Wi-Fi 原厂）..."
mtk w dtbo "$WIFI_DTBO"
echo "==> 禁用 vbmeta 验证（避免 AVB 哈希不匹配拒启）..."
mtk w vbmeta --disable-verification
echo "==> 重启设备 ..."
mtk reset
echo "✓ 完成。首次启动观察：能否到 Android、屏是否亮、按键/触控/Wi-Fi 是否正常。"
