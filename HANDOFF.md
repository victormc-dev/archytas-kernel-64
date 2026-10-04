# 项目交接文档 — Archytas (小爱老师 Wi-Fi 版) 64 位内核移植

> 用途：在另一台电脑（Windows）上继续本项目时，把本对话的完整上下文压缩成可粘贴的提示词。
> 本文件自包含，不依赖历史对话。

---

## 一、项目是什么

把 **archimedes 的 4G 版 64 位 Linux 内核**（MT6761 / k61 平台，Linux 4.9.117）移植到
**小爱老师 Wi-Fi 版**（设备代号 **Archytas**）。两边是同一颗 SoC、同一块 PCB 基线，
所以本质是「换板级设备树 + 处理验证启动」，内核二进制无需改。

- 我们的仓库：`https://github.com/victormc-dev/archytas-kernel-64`（成果都在 `port/`）
- 上游 4G 64 位内核源码：`https://github.com/agui4541/archimedes-kernel-64`
  （默认分支 `main`，含打包模板 `tools/archimedes-boot-template-32MiB.img`）

## 二、已验证的关键结论（铁证来自原厂 ROM 设备树对比）

| 结论 | 依据 |
| --- | --- |
| 4G 版与 Wi-Fi 版**同 PCB / 同 SoC** | 主 DTB 494 节点**零差异**；overlay 288 节点，板级设备 100% 相同 |
| 真正差异只在 overlay 内部 **11 处属性** | 4G 有 SIM 卡 GPIO、按键 debounce/IRQ 参数不同；Wi-Fi 无 SIM 卡槽 |
| **内核二进制无需改** | 驱动通用，仅板级 DTB/overlay 不同 |
| **`dtbo` 分区必须刷 Wi-Fi 原厂** | 刷 4G dtbo 会带错 SIM GPIO + 4G 按键配置 |
| Wi-Fi 原厂启用 **AVB 验证启动** | boot 后段有 `AVB0` 哈希，ROM 含 `vbmeta.bin`；archimedes boot 无 AVB 哈希 |
| 刷 archimedes boot 必须 `mtk vbmeta 2` | 否则 AVB 拒启；禁用只影响校验，不破坏 system/vendor/userdata |
| **32 位 ROM + 64 位内核兼容** | `CONFIG_COMPAT=y`；上游 `BASELINE-20260930.md` 记录 4G 零售机已实机 booted and tested（pure / kernelsu 两变体均进 Android，Wi-Fi/BT/音频/CM3232/CCCI 节点齐全） |
| archimedes 模板特征 | 32MiB、Android v1、2048 字节页、cmdline `bootopt=64S3,32S1,64S1`、模板 DTB SHA256 `7a5af188…bf304` |
| MTKClient 连接密码 | ROM 内 txt 注明 `123456`（必要时 `--auth`） |
| 内核版本 | `4.9.117-agui@caner` |

## 三、`port/` 已交付物

**文档**
- `ARCHYTAS_PORT.md` — 完整移植方案（硬件对比 / 两阶段策略 / AVB 与 32-64 位兼容性 / 刷机 runbook / 修订记录）
- `FLASH_ENV.md` — 刷机环境配置说明（Linux / macOS）
- `README.md`（根目录）— 仓库说明

**脚本（bash）**
- `flash_archytas.sh` — 刷机（强制备份 + 二次确认 + 禁用 vbmeta + 回退）
- `setup_flash_env.sh` — 一键装 MTKClient + 依赖 + udev
- `make_wifi_template.py` — 把 32MiB 模板里的 4G DTB 换成 Wi-Fi 主 DTB
- `build_archytas_wifi.sh` — defconfig→编内核→换 DTB→打包
- `dtb_dump.py` / `dtb_diff.py` / `analyze_oem_diff.py` / `check_ramdisk.py` — 设备树分析工具
- `99-mtk.rules` — 联发科 BROM(`0e8d:0003`)/preloader(`0e8d:7000`) udev 规则

**配置 / 证据**
- `k61v1_64_archytas_defconfig` — archytas defconfig（基于 4G，可选关基带）
- `wifi_stock.dtb`/`.dts`、`4g_oem_boot.dtb`/`4g_oem_dtbo.dtb`、`dtbo_internal.dtb`/`.dts` — 双方原厂抽取物
- `diff_main_oem.txt` / `diff_dtbo_oem.txt` — 主 DTB / overlay 全量 diff 报告

**CI**
- `.github/workflows/build.yml` — GitHub Actions：checkout 本仓库 `port/` + 上游内核源码 → 装 `aarch64-linux-gnu` 交叉编译器 → 复刻 `build_archimedes.sh` 流程编 `Image.gz-dtb` → 打包 Stage1(4G 模板 DTB)/Stage2(Wi-Fi DTB) 两份 boot → 上传 artifact `archytas-wifi-boot`。已修 `scripts/config` 路径（`O=` 输出目录不含该脚本，改用 `$PWD/scripts/config`），并加 `build_stage` 选择（both/stage1/stage2，默认 both）。

## 四、待办 / 下一步

1. **确认 GitHub Actions 编译结果**：看仓库 Actions 标签页。最后改动已修 `scripts/config` 路径并加 stage 选择。若老 4.9 内核在 GCC-11 下编译报错，改用更老的 `aarch64-linux-android-4.9` 工具链或针对性加 `-Wno-xxx`。
2. **下载 artifact** `archytas-wifi-boot`（含 `boot-archytas-wifi-stage1.img` 与 `boot-archytas-wifi.img`/Stage2）。
3. **刷机**（务必先备份）：
   - 进下载模式：关机 → 音量下+电源插 USB，或短接主板 test point；`lsusb`/`usbview` 看到 `0e8d:0003` 或 `0e8d:7000`。
   - 先刷 **Stage1**（4G 模板 DTB）验证同 PCB 能启动；再刷 Stage2（换 Wi-Fi DTB）。
   - `dtbo` **刷 Wi-Fi 原厂**；刷 boot 同步 `mtk vbmeta 2`；沿用 Wi-Fi 原厂 `lk.bin`。
4. **验证**：进 Android、亮屏、触控/Wi-Fi/蓝牙/音频正常。重点排查 **LCM 黑屏**（lk atag 传的面板名 vs 内核 `CONFIG_CUSTOM_KERNEL_LCM` 列表不匹配）。
5. **回退**：随时 `mtk w boot/dtbo/vbmeta <备份>` 刷回，不会硬变砖（preloader/lk 未动）。

## 五、Windows 特注意事项（你转到 Windows）

- **脚本是 bash**：用 **Git Bash** 或 **WSL** 运行；但**实际刷机不要走 WSL 的 USB**（WSL2 的 USB 透传对 MTK BROM 时序不稳，常握手超时）。
- **Windows 原生刷机推荐路径**：装原生 Python → `git clone bkerler/mtkclient` 并装运行依赖（pyusb/pycryptodome/pyserial…）→ 用 **UsbDk** 驱动（官方推荐，非 Zadig/WinUSB）绑 BROM(`0e8d:0003`)/preloader(`0e8d:7000`)；直接用 `python mtk.py` 运行（无需 mtk 命令）。
- 编译已在 GitHub Actions 完成，Windows 机器主要只需**刷机工具**，不必本地交叉编译。
- 若 Windows 上想本地编译，用 WSL + Ubuntu 跑 `setup_flash_env.sh` 最省事（udev 规则在 WSL 内无效，但刷机走原生 Windows USB 时不需要 udev）。

---

## 六、续作提示词（复制到新电脑的 AI 对话框）

```
你正在继续一个内核移植项目。背景如下：

目标：把 archimedes 的 4G 版 64 位 Linux 内核（为「小爱老师」4G 版设备构建）移植到 Wi-Fi 版（设备代号 Archytas）。两边都是联发科 MT6761 (k61 平台)。

仓库：
- 我们的成果：https://github.com/victormc-dev/archytas-kernel-64 （工作都在 port/ 目录）
- 上游 4G 64 位内核源码：https://github.com/agui4541/archimedes-kernel-64 （分支 main，含打包模板 tools/archimedes-boot-template-32MiB.img）

已验证的关键事实（来自原厂 ROM 设备树对比）：
- 4G 版与 Wi-Fi 版共用同一 PCB/SoC。主 DTB 494 节点零差异；overlay(dtbo) 288 节点，板级设备 100% 相同，仅 11 处内部属性差异（4G 有 SIM 卡 GPIO、按键 debounce/IRQ 参数不同；Wi-Fi 无 SIM 卡槽）。
- 因此内核二进制无需改动。真正的板级差异在 dtbo overlay 里。
- 铁律：刷机时 dtbo 分区必须刷 Wi-Fi 原厂 dtbo.bin（不能刷 4G 的），否则带入错误 SIM GPIO 与 4G 按键配置。
- AVB：Wi-Fi 原厂 boot 含 AVB0 验证哈希且带 vbmeta.bin；archimedes boot 无 AVB 哈希。写完 archimedes boot 后必须执行 `mtk vbmeta 2`。这不会破坏 system/vendor/userdata。
- 32 位 Android userspace + 64 位内核可行：CONFIG_COMPAT=y，且上游 BASELINE-20260930.md 记录 4G 零售机已实机启动成功（pure 与 kernelsu 两变体均进 Android，Wi-Fi/BT/音频/CM3232/CCCI 节点齐全）。
- 内核版本 4.9.117。archimedes 模板：32MiB、Android v1、2048 字节页、cmdline bootopt=64S3,32S1,64S1。MTKClient 连接密码 123456。

port/ 已有交付物：
- ARCHYTAS_PORT.md（完整方案）、FLASH_ENV.md（环境配置）、flash_archytas.sh（刷机，含备份+回退）、setup_flash_env.sh（环境安装）、99-mtk.rules（udev）
- make_wifi_template.py（32MiB 模板内 4G DTB→Wi-Fi DTB 替换）、build_archytas_wifi.sh（defconfig→编译→换 DTB→打包）
- dtb_dump.py / dtb_diff.py / analyze_oem_diff.py / check_ramdisk.py（设备树分析工具）
- k61v1_64_archytas_defconfig，以及双方原厂抽取的 DTB/DTS 与 diff 报告
- .github/workflows/build.yml（GitHub Actions：编 Image.gz-dtb，打包 Stage1/Stage2 两份 boot，上传 artifact）

待办 / 下一步：
1. 确认 GitHub Actions 编译结果（最后改动修了 O= 下的 scripts/config 路径，并加了 build_stage 选择 both/stage1/stage2）。若 GCC-11 编译老 4.9 内核报错，换 aarch64-linux-android-4.9 工具链或加 -Wno-xxx。
2. 下载 artifact archytas-wifi-boot（含 boot-archytas-wifi-stage1.img 与 boot-archytas-wifi.img/Stage2）。
3. 刷机（先备份）：进下载模式（关机→音量下+电源插 USB，或短接 test point）；先刷 Stage1 验证同 PCB 启动；dtbo 刷 Wi-Fi 原厂；刷 boot 同步禁用 vbmeta；沿用 Wi-Fi 原厂 lk.bin。
4. 验证：进 Android、亮屏、触控/Wi-Fi/蓝牙/音频正常。排查 LCM 黑屏（lk atag 面板名 vs CONFIG_CUSTOM_KERNEL_LCM）。
5. 视情况把更新 push 回 GitHub 仓库。

Windows 特别注意：脚本是 bash，用 Git Bash/WSL 运行；但刷机不要用 WSL 的 USB。Windows 原生刷机用 Python + `git clone bkerler/mtkclient` 装运行依赖 + **UsbDk** 驱动（非 Zadig/WinUSB），直接 `python mtk.py` 运行。编译已在 Actions 完成，Windows 机器主要只需刷机工具。

请先告诉我你想从哪一步开始，或先帮我校验 GitHub Actions 的构建状态。
```

---

*生成于 2026-10-04，会话从 macOS 转移到 Windows 继续。*
