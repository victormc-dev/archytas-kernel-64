#!/usr/bin/env bash
#
# setup_flash_env.sh — 配置小爱老师 Wi-Fi 版(Archytas) 64 位内核移植的刷机环境
#
# 作用：安装 MTKClient（联发科刷机工具 `mtk`）、其依赖与（Linux）USB 权限，
#       使本机可进入 BROM/下载模式并对设备 boot / dtbo / vbmeta 分区读写。
#
# 用法：
#   chmod +x setup_flash_env.sh
#   ./setup_flash_env.sh            # 自动检测系统并安装
#   ./setup_flash_env.sh --check    # 仅校验当前环境，不安装
#
# 重要：本项目用的 MTKClient 是 bkerler/mtkclient（https://github.com/bkerler/mtkclient）。
#       PyPI 上【没有】名为 mtkclient 的包，必须用源码 `git clone` 后 `pip install .`，
#       或只装纯 Python 运行依赖后用 `python mtk.py` 直接运行（推荐，免装 Qt GUI）。
#       Windows 驱动官方推荐 UsbDk（非 Zadig/WinUSB）；详见仓库 README-WINDOWS.zh-CN.md。
#
set -euo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MTKCLIENT_DIR="${MTKCLIENT_DIR:-$REPO_ROOT/../mtkclient}"

SYS="$(uname -s)"
case "$SYS" in
  Linux*)   PLATFORM=linux ;;
  Darwin*)  PLATFORM=macos ;;
  *MINGW*|*MSYS*|*CYGWIN*) PLATFORM=windows ;;
  *)        PLATFORM=unknown ;;
esac

echo "=== Archytas 刷机环境配置 ==="
echo "检测到系统: $SYS  ->  平台=$PLATFORM"
echo "MTKClient 源码目录: $MTKCLIENT_DIR"
echo ""

# ---- 1. 前置命令检测 -------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 && echo "  [OK] $1" || echo "  [缺失] $1"; }
echo "--- 前置工具 ---"
need python3; need python; need git; need pip3
echo ""

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "--- 仅校验模式：已列出前置工具。系统依赖与 MTKClient 未安装。 ---"
  echo "退出。正式安装请去掉 --check 参数运行。"
  exit 0
fi

# ---- 2. 按系统安装 MTKClient ----------------------------------------------
if [[ "$PLATFORM" == "windows" ]]; then
  echo "--- Windows：MTKClient 以源码方式运行（免 Qt GUI）---"
  echo "  [提示] Windows 还需手动安装：UsbDk 驱动、Winfsp、OpenSSL 1.1.1"
  echo "        详见 mtkclient/README-WINDOWS.zh-CN.md"
  if [[ ! -d "$MTKCLIENT_DIR" ]]; then
    git clone https://github.com/bkerler/mtkclient "$MTKCLIENT_DIR"
  else
    echo "  源码目录已存在，跳过 clone。"
  fi
  echo "--- 安装纯 Python 运行依赖（pyusb/pycryptodome/pyserial 等）---"
  python -m pip install pyusb pycryptodome pycryptodomex colorama pyserial mfusepy
  echo ""
  echo "  完成。刷机时可直接： python \"$MTKCLIENT_DIR/mtk.py\" <命令>"
  echo "  （如需 `mtk` 控制台命令，可改在 $MTKCLIENT_DIR 执行: pip install . ，"
  echo "   但会额外拉取 pyside6 Qt GUI，体积大、且本机 pip 构建清理可能被安全软件拦截）"

elif [[ "$PLATFORM" == "linux" ]]; then
  echo "--- Linux：装系统依赖 + venv + MTKClient ---"
  sudo apt-get update
  sudo apt-get install -y python3 python3-pip python3-venv git \
    libusb-1.0-0 libusb-1.0-0-dev udev build-essential pkg-config
  VENV_DIR="$SCRIPT_DIR/.venv"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then python3 -m venv "$VENV_DIR"; fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip
  if [[ ! -d "$MTKCLIENT_DIR" ]]; then
    git clone https://github.com/bkerler/mtkclient "$MTKCLIENT_DIR"
  fi
  pip install "$MTKCLIENT_DIR"
  RULES=/etc/udev/rules.d/99-mtk.rules
  sudo tee "$RULES" >/dev/null <<'EOF'
# MediaTek BROM / preloader (小爱老师刷机)
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="7000", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
  sudo udevadm control --reload-rules
  sudo udevadm trigger
  echo "  udev 规则已重载。插拔设备后当前用户即可访问（无需 root）。"

elif [[ "$PLATFORM" == "macos" ]]; then
  echo "--- macOS：装 libusb + MTKClient（BROM 时序不稳，建议用 Linux VM）---"
  if command -v brew >/dev/null 2>&1; then brew install libusb; fi
  if [[ ! -d "$MTKCLIENT_DIR" ]]; then
    git clone https://github.com/bkerler/mtkclient "$MTKCLIENT_DIR"
  fi
  pip3 install "$MTKCLIENT_DIR"
  echo "  [!] macOS 上 BROM 握手时序常不稳定，强烈建议改在 Linux 虚拟机/设备刷机。"

else
  echo "  [!] 未识别平台 $SYS，无法自动安装。请参考 https://github.com/bkerler/mtkclient"
  exit 1
fi

echo ""
echo "=== 配置完成 ==="
echo "Windows 用户：用 Git Bash 运行 flash_archytas.sh（已自动回退到 python mtk.py）。"
echo "下一步：进下载模式后运行 flash_archytas.sh，或参考 ARCHYTAS_PORT.md 的刷机 runbook。"
echo "铁律：Wi-Fi 版 dtbo 必须刷 Wi-Fi 原厂；刷 boot 后执行 mtk vbmeta 2 禁用验证（2.1.x 写法）。"
