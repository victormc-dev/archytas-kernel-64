# Archytas Kernel 64 — 小爱老师 Wi-Fi 版 64 位内核移植

将 [archimedes-kernel-64](https://github.com/)（小爱老师 **4G 版** 64 位内核，Linux 4.9.117 / MT6761 / arm64）移植到 **Wi-Fi 版（Archytas）** 硬件的完整分析、工具链与刷机方案。

> 本项目只负责 "4G 版 64 位内核 → Wi-Fi 版硬件" 的移植工作；内核源码树与 ROM 固件不属于本仓库（体积与版权原因，见下文"仓库范围"）。

---

## 1. 背景与核心结论

两个版本（4G 版、Wi-Fi 版小爱老师）**硬件高度同源**：同为联发科 **MT6761** SoC、同 PCB 基线。通过**原厂对原厂**设备树对比（`4G原厂 boot.bin+dtbo.bin` vs `Wi-Fi原厂 boot.bin+dtbo.bin`）确认：

| 对比项 | 4G 原厂 | Wi-Fi 原厂 | 差异 |
| --- | --- | --- | --- |
| 主 DTB 节点数 | 494 | 494 | **0 差异**（status/compatible/reg/内部属性全同） |
| Overlay（dtbo）节点数 | 288 | 288 | 板级设备集合 **100% 相同** |
| Overlay 内部属性 | — | — | **仅 11 处**：`fragment@36` 的 6 个 SIM GPIO（Wi-Fi 无 SIM 卡槽，为 `<none>`）+ `fragment@39/@49` 按键 debounce/中断参数 |
| PMIC / 音频 / 连接 | MT6357 / mt6357+AW87329 / CONSYS_6761 combo（Wi-Fi·BT·GPS） | 完全相同 | — |

**结论**：两边几乎同一套设备树，移植本质 = **换板级 DTB（dtbo 分区用 Wi-Fi 原厂） + 可选关蜂窝基带**，内核二进制无需改动。

其他关键事实（详见 `port/ARCHYTAS_PORT.md`）：
- **AVB 验证**：Wi-Fi 原厂启用 Android Verified Boot（boot 含 `AVB0` 哈希 + 有 `vbmeta.bin`）；archimedes 内核无 AVB 哈希，刷入需 `mtk w vbmeta --disable-verification`。
- **32/64 位兼容**：原厂 ROM 的 userspace 是 32 位 arm，archimedes 是 64 位 arm64 内核；`CONFIG_COMPAT=y` 保证 64 位内核可启动 32 位 `/init`，且 4G 版已实机验证进 Android 成功。
- **system-as-root**：boot 镜像无传统 ramdisk，根文件系统来自 `system` 分区，刷机只碰 `boot`/`dtbo`/`vbmeta`，不影响系统数据。

---

## 2. 仓库范围

本仓库**仅包含移植成果**，不含以下大体积/版权受限内容（已被 `.gitignore` 排除）：
- `archimedes-kernel-64-main/`（内核源码树，5.6 万+ 文件）
- `*原包*/`（4G / Wi-Fi 版原始 ROM 固件镜像）
- `*.img`（32MiB 模板等中间产物）

如需复现分析，请自备上述内核树与 ROM，按其原始许可使用。

---

## 3. 文件结构

```
port/
├── ARCHYTAS_PORT.md         # 完整移植方案（硬件对比 / 两阶段策略 / AVB / 32-64位 / 刷机 runbook）
├── k61v1_64_archytas_defconfig  # Wi-Fi 版 defconfig（基于 4G 配置，含可选关基带说明）
├── build_archytas_wifi.sh   # 一键构建：拷贝 defconfig → 编 Image.gz-dtb → 换 DTB → 打包
├── make_wifi_template.py    # 将 32MiB 模板里的 4G 主 DTB 替换为 Wi-Fi 主 DTB
├── flash_archytas.sh        # MTKClient 刷机脚本（强制备份 / 禁用 vbmeta / 回退）
│
├── dtb_dump.py              # 纯 Python DTB→DTS 反编译器（无 dtc 依赖）
├── dtb_diff.py              # 节点级全量 DTB diff
├── analyze_oem_diff.py      # 4G 与 Wi-Fi 原厂设备树对比
├── check_ramdisk.py         # boot ramdisk / AVB 验证核查
│
├── wifi_stock.dtb / .dts    # Wi-Fi 原厂主 DTB 及反编译（移植依据）
├── 4g_oem_boot.dtb          # 4G 原厂主 DTB
├── 4g_oem_dtbo.dtb          # 4G 原厂 overlay
├── dtbo_internal.dtb / .dts # Wi-Fi 原厂 dtbo 内部提取的 DTB/overlay
├── 4g_stock.dtb / .dts      # archimedes 模板内 4G DTB（供参照）
└── diff_main_oem.txt / diff_dtbo_oem.txt  # 主 DTB / overlay 全量差异报告
```

---

## 4. 快速开始

### 4.1 构建（在 Linux 构建机，需 `aarch64-linux-gnu-gcc` / `make` / `python3`）

```sh
# Stage 1（最快验证同根，推荐先跑）：4G 模板内核 + Wi-Fi 原厂 dtbo
cd archimedes-kernel-64-main
JOBS=24 ./build_archimedes.sh pure
python3 package_archimedes_boot.py \
  --template tools/archimedes-boot-template-32MiB.img \
  --kernel out-archimedes-pure/arch/arm64/boot/Image.gz-dtb \
  --output boot-archytas-wifi-stage1.img

# Stage 2（正规移植）：换 Wi-Fi 主 DTB 后构建
cd ../port && chmod +x build_archytas_wifi.sh
./build_archytas_wifi.sh          # 产出 port/boot-archytas-wifi.img
```

### 4.2 刷入（MTKClient；设备进下载模式 BROM：`音量下+电源` 或短接 test point）

```sh
mtk payload                         # ROM 内 txt 注密码 123456，按需 --auth
mtk partition getboot               # 确认 boot / dtbo 分区名

# 先备份，再刷（备份是救命用的）
mtk r boot boot_bak.img
mtk r dtbo dtbo_bak.img

# 刷：boot 用移植产物，dtbo【必须】用 Wi-Fi 原厂
mtk w boot boot-archytas-wifi.img   # 或 stage1 镜像
mtk w dtbo "../柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/dtbo.bin"
mtk w vbmeta --disable-verification   # ⚠️ 关闭 AVB 验证，否则启动被拒
mtk reset
```

### 4.3 回退

```sh
mtk payload
mtk w boot boot_bak.img
mtk w dtbo dtbo_bak.img
mtk w vbmeta vbmeta_bak.img         # 若此前备份过原 vbmeta
mtk reset
```

以上步骤已封装进 `port/flash_archytas.sh`（带二次确认与备份校验），拷贝到构建机后改顶部 3 个路径变量即可运行。

---

## 5. 关键风险与注意

1. **`dtbo` 必须刷 Wi-Fi 原厂**：4G 的 dtbo 含 SIM GPIO 与 4G 按键参数，刷到 Wi-Fi 板会导致按键错乱 / 引入无用 SIM 引脚。
2. **必须禁用 vbmeta 验证**：否则 archimedes 内核的 boot 无法通过 AVB 校验。禁用只影响启动校验，不破坏 `system` 数据。
3. **LCM 黑屏**：面板名由 `lk` 经 atag 传入内核；黑屏但能 `adb` 多为 lk 面板名与内核 `CONFIG_CUSTOM_KERNEL_LCM` 不匹配。**沿用 Wi-Fi 原厂 `lk.bin`**，勿动 lk。
4. **启动失败 ≠ 变砖**：`preloader`/`lk` 未动，`system`/`vendor`/`data` 完好，随时刷回备份即可恢复。

---

## 6. 参考

- 完整方案与证据：`port/ARCHYTAS_PORT.md`
- 原厂设备树差异报告：`port/diff_main_oem.txt` / `port/diff_dtbo_oem.txt`

---
*免责声明：本项目仅供研究与原厂设备学习使用。刷机有风险，操作前务必备份。固件与内核源码的版权归各自所有者。*
