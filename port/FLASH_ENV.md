# 刷机环境配置（Archytas / 小爱老师 Wi-Fi 版）

把 64 位 archimedes 内核刷入 Wi-Fi 版设备，需要一台能跑 **MTKClient** 的机器，
并通过 USB 进入联发科 **BROM / 下载模式**。本目录提供一键配置脚本。

> ⚠️ 本项目用的 MTKClient 是 **bkerler/mtkclient**（https://github.com/bkerler/mtkclient）。
> PyPI 上**没有**名为 `mtkclient` 的包，`pip install mtkclient` 会失败。
> 必须 `git clone` 后用 `pip install .`，或只装纯 Python 运行依赖后直接 `python mtk.py` 运行。

## 1. 设备与模式
- SoC：MT6761（k61 平台），USB 厂号 `0e8d`。
- 下载模式两种 VID:PID：
  - BROM：`0e8d:0003`
  - preloader：`0e8d:7000`
- 进模式通用法：关机 → 按住「音量下 + 电源」插 USB；或短接主板 test point 再插 USB。
- Linux `lsusb` / Windows 设备管理器（应闪现 `MediaTek USB Port` / `0e8d:0003`）可确认。

## 2. 自动配置（推荐）
```sh
cd port
chmod +x setup_flash_env.sh
./setup_flash_env.sh            # 自动检测系统并安装 MTKClient + 依赖
./setup_flash_env.sh --check    # 仅校验当前环境，不安装
```
脚本会按平台：
- **Windows**：`git clone` bkerler/mtkclient，安装纯 Python 运行依赖（pyusb/pycryptodome/pyserial…），
  刷机时由 `flash_archytas.sh` 自动回退到 `python <MTKCLIENT_DIR>/mtk.py`（免装 Qt GUI）。
- **Linux**：建 `port/.venv`，`pip install .` 装 `mtk`，并写 udev 规则（普通用户可刷）。
- **macOS**：装 libusb + `pip install .`（BROM 时序不稳，建议改用 Linux）。

## 3. 手动配置（Windows，推荐路径）
```powershell
# 1) 装 Python >=3.9（勾 Add to PATH）、Git
# 2) 装驱动（官方推荐 UsbDk，非 Zadig）：
#    - UsbDk 64-bit .msi：https://github.com/daynix/UsbDk/releases/
#    - Winfsp：https://winfsp.dev/rel/   （fuse 用）
#    - OpenSSL 1.1.1：https://sourceforge.net/projects/openssl-for-windows/
# 3) 克隆并装运行依赖（免 Qt GUI，直接 python mtk.py 运行）
git clone https://github.com/bkerler/mtkclient C:/mtkclient
cd C:/mtkclient
pip install pyusb pycryptodome pycryptodomex colorama pyserial mfusepy
# 4) 验证（无需设备）
python mtk.py --help
# 进下载模式后，用 UsbDkController -n 应能看到 0x0E8D 0x0003
```
> 切勿用 WSL 的 USB 刷机（WSL2 的 USB 透传对 MTK BROM 握手不稳，常超时）。
> 走 Windows 原生 Python + UsbDk 驱动最稳。

## 4. 手动配置（Linux）
```sh
sudo apt-get install -y python3 python3-pip python3-venv git libusb-1.0-0 udev
python3 -m venv port/.venv && source port/.venv/bin/activate
pip install --upgrade pip
git clone https://github.com/bkerler/mtkclient "$PWD/../mtkclient"
pip install "$PWD/../mtkclient"     # 得到 mtk 命令
sudo cp port/99-mtk.rules /etc/udev/rules.d/99-mtk.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
mtk --help
```

## 5. 手动配置（macOS，可行但不稳）
> BROM 模式在 macOS USB 栈上时序不稳，常出现「握手超时 / 找不到设备」。**强烈建议用 Linux。**
```sh
brew install libusb
git clone https://github.com/bkerler/mtkclient ~/mtkclient
pip3 install ~/mtkclient
mtk --help
```

## 6. 验证 MTKClient 可用
```sh
mtk --help          # 或 python <MTKCLIENT_DIR>/mtk.py --help
# 应列出 printgpt / r / w / reset / payload 等子命令（注意：没有 partition 子命令）
```
设备进下载模式后（无需显式 payload，r/w 会自动 exploit BROM）：
```sh
mtk printgpt        # 列出分区，确认有 boot / dtbo / vbmeta
```

## 7. 与刷机脚本衔接
环境就绪后，用 `flash_archytas.sh`（同目录）刷机：
- 刷前强制备份原 `boot` / `dtbo` / `vbmeta`；
- `dtbo` **必须刷 Wi-Fi 原厂**（含「无 SIM + Wi-Fi 按键配置」），勿用 4G；
- 刷 `boot` 后执行 **`mtk vbmeta 2`**（2.1.x 写法）禁用验证启动，不影响 system/vendor/userdata；
- 沿用 Wi-Fi 原厂 `lk.bin`，勿动 lk（避免 LCM 黑屏）。

完整 runbook 见 `ARCHYTAS_PORT.md` 第 5 节。
