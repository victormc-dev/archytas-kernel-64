#!/usr/bin/env bash
# ============================================================================
# flash_archytas.sh —— 小爱老师 Wi-Fi 版（Archytas）64 位内核刷入脚本
# 运行环境：Git Bash（Windows）/ bash（Linux/macOS），设备已进下载模式
# 安全策略：刷前【强制备份】原 boot/dtbo/vbmeta，写操作前【二次确认】。
#
# 依赖：MTKClient（bkerler/mtkclient，>=2.0）。本项目以源码方式运行：
#       git clone https://github.com/bkerler/mtkclient 后，脚本会自动回退到
#       `python <MTKCLIENT_DIR>/mtk.py`；若已 `pip install .` 得到 `mtk` 命令则优先用。
#
# 两阶段刷机策略（见 ARCHYTAS_PORT.md）：
#   首次务必先刷 Stage1（4G 模板 DTB）验证“同 PCB 能点亮”，再刷 Stage2（Wi-Fi DTB）。
#   例：BOOT_IMG=archytas-wifi-boot/boot-archytas-wifi-stage1.img bash flash_archytas.sh
# ============================================================================
set -euo pipefail

# ---- 1. 路径配置（默认按仓库根相对解析，一般无需改）-----------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# MTKClient 源码目录（git clone 的位置）；为空则尝试 PATH 上的 `mtk` 命令
MTKCLIENT_DIR="${MTKCLIENT_DIR:-$REPO_ROOT/../mtkclient}"
# 若没传 MTKCLIENT_DIR，回退到常见位置
if [[ ! -f "$MTKCLIENT_DIR/mtk.py" ]]; then
  MTKCLIENT_DIR="${MTKCLIENT_DIR:-/d/Downloads/mtkclient}"
fi

# 选择 mtk 命令：优先 PATH 上的 `mtk`，否则源码运行 `python mtk.py`
if command -v mtk >/dev/null 2>&1; then
  MTK="mtk"
else
  if [[ -f "$MTKCLIENT_DIR/mtk.py" ]]; then
    MTK="python \"$MTKCLIENT_DIR/mtk.py\""
  else
    echo "✗ 未找到 mtk 命令，也未找到 $MTKCLIENT_DIR/mtk.py"
    echo "  请先: git clone https://github.com/bkerler/mtkclient 并装依赖(pyusb pycryptodome pyserial)"
    exit 1
  fi
fi

# archimedes 移植产物（boot 镜像）。Stage1 / Stage2 二选一，由 BOOT_IMG 指定。
BOOT_IMG="${BOOT_IMG:-$REPO_ROOT/archytas-wifi-boot/boot-archytas-wifi.img}"   # 默认 Stage2(最终)
# Wi-Fi 原厂 dtbo（【必须】是 Wi-Fi 原厂，不要 4G 的！4G 的会带错 SIM GPIO）
WIFI_DTBO="${WIFI_DTBO:-$REPO_ROOT/柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/dtbo.bin}"
# 备份存放目录
BAK_DIR="${BAK_DIR:-./bak_$(date +%Y%m%d_%H%M%S)}"
# 如需 SLA/DAA 授权（ROM txt 注明 123456），设此项： MTK_AUTH="--auth 123456"
MTK_AUTH="${MTK_AUTH:-}"

mtk_run() { eval "$MTK" "$@"; }

# ---- 2. 预检 ---------------------------------------------------------------
echo "==> 使用 MTK 命令: $MTK"
[[ -f "$BOOT_IMG" ]] || { echo "✗ 找不到 boot 镜像: $BOOT_IMG"; exit 1; }
[[ -f "$WIFI_DTBO" ]] || { echo "✗ 找不到 Wi-Fi 原厂 dtbo: $WIFI_DTBO（务必用 Wi-Fi 原厂，勿用 4G）"; exit 1; }
echo "==> boot 镜像: $BOOT_IMG"
echo "==> dtbo 镜像: $WIFI_DTBO (Wi-Fi 原厂)"
echo ""
echo "!! 确认设备已进入下载模式（关机→音量下+电源插 USB，或短接 test point）"
echo "   Windows 看到 0e8d:0003(BROM) 或 0e8d:7000(preloader)；按提示接好线。"
echo ""

# ---- 3. 强制备份（含 vbmeta）---------------------------------------------
mkdir -p "$BAK_DIR"
echo "==> 强制备份原 boot / dtbo / vbmeta 到 $BAK_DIR ..."
mtk_run r boot   "$BAK_DIR/boot_bak.img"   $MTK_AUTH
mtk_run r dtbo   "$BAK_DIR/dtbo_bak.img"   $MTK_AUTH
mtk_run r vbmeta "$BAK_DIR/vbmeta_bak.img" $MTK_AUTH
echo "✓ 备份完成。回退:"
echo "    $MTK r boot   $BAK_DIR/boot_bak.img"
echo "    $MTK r dtbo   $BAK_DIR/dtbo_bak.img"
echo "    $MTK r vbmeta $BAK_DIR/vbmeta_bak.img"

# ---- 4. 二次确认 -----------------------------------------------------------
echo ""
echo "即将写入："
echo "  boot  -> $BOOT_IMG"
echo "  dtbo  -> $WIFI_DTBO   (Wi-Fi 原厂，勿换 4G)"
echo "  vbmeta-> mode 2 (disable_verification，禁用 AVB 验证；不影响 system/vendor/userdata)"
echo ""
read -r -p "确认刷入? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消，未写入。"; exit 0; }

# ---- 5. 写入 ---------------------------------------------------------------
echo "==> 刷 boot ..."
mtk_run w boot "$BOOT_IMG" $MTK_AUTH
echo "==> 刷 dtbo（Wi-Fi 原厂）..."
mtk_run w dtbo "$WIFI_DTBO" $MTK_AUTH
echo "==> 禁用 vbmeta 验证（archimedes boot 无 AVB 哈希，需禁用验证避免拒启）..."
mtk_run vbmeta 2 $MTK_AUTH
echo "==> 重启设备 ..."
mtk_run reset
echo "✓ 完成。首次启动观察：能否到 Android、屏是否亮、按键/触控/Wi-Fi/蓝牙/音频是否正常。"
echo "  若黑屏：重点排查 lk atag 面板名 vs 内核 CONFIG_CUSTOM_KERNEL_LCM。"
echo "  验证 OK 后再刷 Stage2（换 BOOT_IMG 为 boot-archytas-wifi-stage1.img 的反面）。"
