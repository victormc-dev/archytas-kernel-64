#!/usr/bin/env bash
# ============================================================================
# flash_wifi64.sh —— 小爱老师 Wi-Fi 版（Archytas）「64 位 ROM 整合包」刷入脚本
#
# 环境：Git Bash（Windows）/ bash（Linux/macOS）。设备需先进【下载模式】。
# 安全：默认【强制备份】boot/dtbo/vbmeta（小、快）；vendor/system 用 FULL_BACKUP=1 备份。
#       写入前【二次确认】。preloader / lk 一律不动。
#
# 依赖：MTKClient（bkerler/mtkclient）。优先用 PATH 上的 `mtk`，
#       否则回退到源码： MTKCLIENT_DIR=/path/to/mtkclient bash flash_wifi64.sh
#       ROM 内注明的连接密码： MTK_AUTH="--auth 123456"
#
# 用法：
#   bash flash_wifi64.sh                 # 正常刷入
#   BACKUP=1 FULL_BACKUP=1 bash flash_wifi64.sh
#   MTK_AUTH="--auth 123456" bash flash_wifi64.sh
# ============================================================================
set -uo pipefail

PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
IMGDIR="$PKG_DIR/img"
STOCKDIR="$PKG_DIR/stock"

MTKCLIENT_DIR="${MTKCLIENT_DIR:-$PKG_DIR/../mtkclient}"
[[ -f "$MTKCLIENT_DIR/mtk.py" ]] || MTKCLIENT_DIR="${MTKCLIENT_DIR:-/d/Downloads/mtkclient}"

if command -v mtk >/dev/null 2>&1; then
  MTK="mtk"
elif [[ -f "$MTKCLIENT_DIR/mtk.py" ]]; then
  MTK="python $MTKCLIENT_DIR/mtk.py"
else
  echo "X 找不到 mtk 命令，也没有 $MTKCLIENT_DIR/mtk.py"
  echo "  请先: git clone https://github.com/bkerler/mtkclient && pip install -r requirements.txt"
  exit 1
fi
mtk_run() { $MTK "$@"; }

MTK_AUTH="${MTK_AUTH:-}"
BACKUP="${BACKUP:-0}"
FULL_BACKUP="${FULL_BACKUP:-0}"
BAK_DIR="${BAK_DIR:-$PKG_DIR/bak_$(date +%Y%m%d_%H%M%S)}"

# ---- 预检 ------------------------------------------------------------------
echo "==> MTK 命令: $MTK"
missing=0
for f in "$IMGDIR/boot.img" "$IMGDIR/vendor.img" "$IMGDIR/system.img" "$STOCKDIR/dtbo.img"; do
  if [[ ! -f "$f" ]]; then echo "X 缺少 $f"; missing=1; fi
done
[[ "$missing" == "0" ]] || exit 1

echo ""
echo "================== 将要写入 =================="
printf "  %-8s -> %s\n" boot   "$IMGDIR/boot.img      (64 位内核 4.9.117 + Wi-Fi DTB，含 consys 修复)"
printf "  %-8s -> %s\n" dtbo   "$STOCKDIR/dtbo.img    (Wi-Fi 原厂 overlay，1754)"
printf "  %-8s -> %s\n" vendor "$IMGDIR/vendor.img    (Wi-Fi 原厂 1754 + /lib64 图形栈 + arm64 连通性模块)"
printf "  %-8s -> %s\n" system "$IMGDIR/system.img    (arm64 GSI，1.396 GiB，无需扩容 system 分区)"
printf "  %-8s -> %s\n" vbmeta "disable verity+verification (模式 3)"
echo "  然后：清 userdata + metadata（换 system 后【必须】清数据）"
echo "  不动：   preloader / lk / lk2 / md1img / nvram / nvdata / protect*"
echo "============================================="
echo ""
echo "!! 请确认设备已进下载模式：关机 -> 按住音量下 + 插 USB"
echo "   Windows 设备管理器应出现 0e8d:0003 (BROM) 或 0e8d:7000 (preloader)"
echo ""
read -r -p "确认继续? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消，未做任何写入。"; exit 0; }

# ---- 建立 BROM 连接 --------------------------------------------------------
echo "==> 建立连接（payload）..."
mtk_run payload $MTK_AUTH || { echo "X payload 失败，检查线缆/驱动/密码"; exit 1; }

# ---- 备份 ------------------------------------------------------------------
if [[ "$BACKUP" == "1" ]]; then
  mkdir -p "$BAK_DIR"
  echo "==> 备份 boot / dtbo / vbmeta -> $BAK_DIR"
  mtk_run r boot   "$BAK_DIR/boot_bak.img"   $MTK_AUTH
  mtk_run r dtbo   "$BAK_DIR/dtbo_bak.img"   $MTK_AUTH
  mtk_run r vbmeta "$BAK_DIR/vbmeta_bak.img" $MTK_AUTH
  if [[ "$FULL_BACKUP" == "1" ]]; then
    echo "==> 全量备份 vendor / system（较慢，vendor 400 MiB / system 1.4 GiB）"
    mtk_run r vendor "$BAK_DIR/vendor_bak.img" $MTK_AUTH
    mtk_run r system "$BAK_DIR/system_bak.img" $MTK_AUTH
  fi
else
  echo "==> 跳过备份（BACKUP=1 可开启）。包内 stock/ 已含原厂 dtbo/lk/vbmeta/boot 可回退。"
fi

# ---- 写入 ------------------------------------------------------------------
echo "==> 刷 boot ..."
mtk_run w boot "$IMGDIR/boot.img" $MTK_AUTH || { echo "X boot 写入失败"; exit 1; }
echo "==> 刷 dtbo（Wi-Fi 原厂）..."
mtk_run w dtbo "$STOCKDIR/dtbo.img" $MTK_AUTH || { echo "X dtbo 写入失败"; exit 1; }
echo "==> 刷 vendor（64 位适配版）..."
mtk_run w vendor "$IMGDIR/vendor.img" $MTK_AUTH || { echo "X vendor 写入失败"; exit 1; }
echo "==> 刷 system（arm64 GSI）..."
mtk_run w system "$IMGDIR/system.img" $MTK_AUTH || { echo "X system 写入失败"; exit 1; }

echo "==> 关闭 AVB（verity + verification）..."
mtk_run da vbmeta 3 $MTK_AUTH || mtk_run vbmeta 3 $MTK_AUTH || \
  echo "!! vbmeta 命令失败（老版 mtkclient 语法不同），若开不了机请手动执行 mtk da vbmeta 3"

# ---- 清数据（必须）--------------------------------------------------------
echo "==> 清 userdata / metadata（换 system 后必须清，否则无法开机）..."
mtk_run e userdata $MTK_AUTH || echo "!! userdata 清除失败"
mtk_run e metadata $MTK_AUTH || echo "!! metadata 清除失败"

echo "==> 重启 ..."
mtk_run reset $MTK_AUTH

cat <<'EOT'

✓ 写入完成。首次开机较慢（首次启动要重建 data + 加载 GSI），耐心等 2~5 分钟。

自检清单：
  1. 屏幕是否亮（黑屏但能 adb：多为面板名不匹配，与内核 lk atag 有关）
  2. adb shell getprop ro.product.cpu.abilist   -> 应为 arm64-v8a,armeabi-v7a,armeabi
  3. adb shell getprop ro.zygote                -> 应为 zygote64_32
  4. adb shell getprop ro.product.cpu.abi       -> 应为 arm64-v8a
  5. adb shell ls /vendor/lib64                 -> 应列出 libusc.so 等 29 个文件
  6. Wi-Fi：设置里连网；驱动侧 adb shell dmesg | grep -i "Find .* in .* BSSes"

回退原厂（64 位 -> 32 位原厂）：
  mtk w boot   stock/boot-stock-1754.img
  mtk w dtbo   stock/dtbo.img
  mtk w vendor <你备份的 vendor 或原厂 vendor>
  mtk w system <你备份的 system 或原厂 system>
  mtk e userdata ; mtk e metadata ; mtk reset
EOT
