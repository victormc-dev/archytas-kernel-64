# 小爱老师 Wi-Fi 版（Archytas）内核移植方案

> 把 `archimedes-kernel-64-main`（小爱老师 4G 版 64 位内核，Linux 4.9.117 / MT6761）
> 移植到 **Wi-Fi 版** 硬件。
> - 4G 版完整 ROM：`柴提全原无数据4G_9.1864/4G原包/`
> - Wi-Fi 版完整 ROM：`柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/`

---

## 0. 修订记录（重要：之前结论已修正）

本文档早期版本基于"archimedes 4G 模板 DTB vs Wi-Fi 原厂主 DTB"的抽样对比，得出了**错误**的中间结论（"Wi-Fi 版关了 msdc2/3/dsi_te/rt9465_slave_chr""板级差异在 overlay 全节点"）。
拿到 **4G 原厂 ROM** 后，做了**原厂对原厂**的权威对比，结论已彻底修正，见第 1 节。

---

## 1. 核心结论（原厂对原厂，权威）

**4G 版与 Wi-Fi 版是同一颗 SoC、同一块 PCB、同一套设备树基线。两者设备树差异极小。**

证据：分别从双方原厂 `boot.bin`（主 DTB）与 `dtbo.bin`（overlay）抽设备树，做节点级全量 diff。

| 维度 | 4G 原厂 | Wi-Fi 原厂 | 差异 |
|---|---|---|---|
| 主 DTB 节点数 | 494 | 494 | **完全相同**：status / compatible / reg / 内部属性全 0 差异 |
| Overlay 节点数 | 288 | 288 | 板级设备集合完全相同；**仅 11 处内部属性不同** |
| SoC | MT6761 | MT6761 | 同 |
| PMIC / 音频 / 连接 / 显示 / 触控 | 一致 | 一致 | 同（AW87329 PA、CONSYS_6761、DSI） |
| 板级外设（LED/振动/充电 bq2560x+rt9465/相机/音频smartpa/NFC/USB-C/GPS LNA/传感器） | 同一组 70+ 节点 | 同一组 70+ 节点 | **集合 100% 一致** |

**唯一真实差异（overlay 内部 11 处属性，其余 2 处是反编译器表示差异非真差异）：**

| 位置 | 4G 原厂 | Wi-Fi 原厂 | 含义 |
|---|---|---|---|
| `fragment@36` 的 6 个 SIM GPIO（SIM1/SIM2 的 SRST/SCLK/SIO） | 有具体 GPIO（0x27/0x25/0x28/0x23/0x24/0x26） | `<none>` | **4G 有 SIM 卡槽，Wi-Fi 无**（无蜂窝射频前端） |
| `fragment@39` 的 `deb-gpios` / `debounce` / `interrupts` | 有 `deb-gpios`、`debounce=0x3e8`(1000ms)、`interrupts=<1 4 1 0>` | 无 `deb-gpios`、`debounce` 缺省、`interrupts=<1 2 1 0>` | 某按键的 debounce / 中断号不同 |
| `fragment@49` 的 `debounce` / `interrupts` | `debounce=<0 0x186a0>`(100000)、`interrupts=<0 8>` | `debounce=<0 0x2710>`(10000)、`interrupts=<0 4>` | 另一处按键 debounce / 中断号不同 |

**一句话**：两版硬件几乎一模一样，差别就是"4G 多了 SIM 卡引脚、按键配置参数不同"。

> 推论：archimedes 4G 内核的驱动 Wi-Fi 版**全部用得上**，内核二进制无需改动。真正决定"在 Wi-Fi 板上跑得对不对"的，是 **dtbo（overlay）分区**——它必须刷 **Wi-Fi 原厂 dtbo**（含"无 SIM + Wi-Fi 按键配置"），**绝不能用 4G 的 dtbo**（4G 的会带上 SIM GPIO 和 4G 按键参数）。

---

## 2. 移植原理（修正）

`archimedes` 打包器 `package_archimedes_boot.py`：模板 `tools/archimedes-boot-template-32MiB.img` 提供 MTK v1 头、ramdisk、以及一份 4G 主 DTB；打包时只取编译产物的 gzip 内核，**强制用模板里的 DTB**。

- **主 DTB 换不换都行**：4G 与 Wi-Fi 原厂主 DTB 完全相同（archimedes 编译版仅 `wifi-reserve-memory` 预留方式 + `charger` 限流参数略有不同，对 Wi-Fi 板无害）。
- **关键在 dtbo 分区**：overlay 才是板级差异载体。移植时必须保证运行中的 overlay 是 **Wi-Fi 原厂 dtbo**。

`make_wifi_template.py` 负责把模板里的 4G 主 DTB 换成 Wi-Fi 原厂主 DTB（可选、进一步贴近原厂），保留头/cmdline/ramdisk/32MiB 尺寸。已通过结构校验。

### 2.1 关于 ramdisk 与 AVB（2026-10-02 核查补充）
抽取双方 `boot` 镜像后段核查，得到两个对"刷机是否影响安卓"至关重要的结论：

- **archimedes 32MiB 模板**：kernel 之后 23MiB **全为 0x00 padding，无 ramdisk、无 AVB 哈希** → archimedes 作者关闭了验证启动（verity/vbmeta 不依赖 boot 内哈希）。
- **Wi-Fi 原厂 boot**：kernel 之后首个非零字节为 `AVB0`（0x41564230），即 Android Verified Boot 哈希描述符 → **Wi-Fi 原厂启用 AVB 验证启动**；ROM 内确有 `vbmeta.bin` 分区。
- 双方 boot 后段均**不含传统 ramdisk（init.rc/fstab 等）**，印证 Android 9 / 4.9 内核设备为 **system-as-root**：根文件系统来自 `system` 分区，boot 镜像只提供内核 + DTB，不负责挂载根。

> 推论：刷 archimedes 内核**不会**因 boot 内 ramdisk 与 `system` 不兼容而出问题（根在 `system` 分区，我们没刷它）。但 **AVB 验证是启动成败的关键约束**——见第 6 节与 5.3。

### 2.2 64 位内核 + 32 位 userspace 架构兼容性（用户确认原厂 ROM 均为 32 位）
用户指出：原厂 4G 版与 Wi-Fi 版 ROM 均为 **32 位 arm** userspace（Android 的 `/init`、HAL、App 均为 32 位 ELF），而 archimedes 是 **64 位 arm64 内核**。核查结论：**此组合已被 archimedes 在 4G 版上实机验证可行，移植到 Wi-Fi 版无架构兼容性新增风险。**

证据：
- `k61v1_64_archimedes_defconfig` 含 `CONFIG_COMPAT=y`（arm64 的 "Kernel support for 32-bit ELF binaries"）→ 64 位内核可执行 32 位 ARM ELF，32 位 `/init` 与全部 32 位 userspace 能正常启动。
- `BASELINE-20260930.md` 明确记录：pure 与 kernelsu 两个 64 位内核变体均 **`booted and tested`**、**`reached Android without a boot loop`**，并在 4G 版硬件上测试了 Wi-Fi/蓝牙/音频 —— 即 **64 位内核 + 4G 版 32 位 ROM userspace 已实机跑通 Android**。
- archimedes 内核 cmdline 为 `bootopt=64S3,32S1,64S1`（MTK 内存段布局，末段 `64S1` 为 64 位内核专用）；Wi-Fi 原厂 boot cmdline 末段为 `32S1`。**刷 archimedes boot 时 cmdline 来自 archimedes 镜像本身**，原厂 `32S1` 不生效，故无需担心该差异。
- archimedes 为完整内核树（驱动编进内核 / 64 位 `.ko`），不依赖原厂 32 位 `.ko`；Wi-Fi 原厂 `vendor` 分区的 32 位 `.ko` 是给原厂内核的，移植时不用。

> 推论：移植到 Wi-Fi 版只换板级 DTB（`dtbo` 用 Wi-Fi 原厂），内核 / 架构 / `CONFIG_COMPAT` 配置完全不变，32/64 混合兼容性层面**零新增风险**。仍须处理的是 AVB（§6）与 `dtbo` 来源（§1），与位数无关。

---

## 3. 两阶段策略

### Stage 1 —— 最快验证（推荐先跑）
用 archimedes 4G 模板（含其编译好的主 DTB）打包 4G 内核，刷 Wi-Fi 板 `boot` 分区，并把 `dtbo` 分区刷成 **Wi-Fi 原厂 dtbo.bin**。
- 目的：确认"archimedes 4G 内核 + Wi-Fi 板级 overlay"能启动到 Android。
- 因为主 DTB 与 overlay 板级设备集合完全一致，风险极低。

### Stage 2 —— 正规构建（长期维护）
- 可选用 `make_wifi_template.py` 把主 DTB 换成 Wi-Fi 原厂主 DTB（更贴近原厂）。
- 用 `k61v1_64_archytas_defconfig`（=4G 配置；如需纯 Wi-Fi 体验，按顶部注释 `scripts/config --disable` 关 CCCI/MD1，但注意 CONSYS 的 Wi-Fi/BT/GPS 保持开启）。
- `dtbo` 分区**始终刷 Wi-Fi 原厂 dtbo.bin**。

> 内核 `Image` 二进制两种阶段都一样；区别只在（可选的）主 DTB 来源与 dtbo 分区。

---

## 4. 交付物清单（均在 `port/`）

| 文件 | 作用 |
|---|---|
| `wifi_stock.dtb` / `wifi_stock.dts` | Wi-Fi 原厂 `boot.bin` 主 DTB 及反编译（84032 字节，ground truth） |
| `dtbo_internal.dtb` / `dtbo_internal.dts` | Wi-Fi 原厂 `dtbo.bin` 内部 overlay 及反编译（38759 字节） |
| `4g_oem_boot.dtb` / `4g_oem_dtbo.dtb` | 4G 原厂 主 DTB / overlay（用于"原厂对原厂"对比） |
| `make_wifi_template.py` | 把 4G 模板主 DTB 换成 Wi-Fi 主 DTB，产出 32MiB 模板 |
| `k61v1_64_archytas_defconfig` | Wi-Fi 版内核配置（=4G 配置，含可选关基带说明） |
| `build_archytas_wifi.sh` | 一站式：拷贝 defconfig → 编内核 → 换 DTB → 打包 |
| `analyze_oem_diff.py` | 原厂对原厂设备树差异分析（抽取 4G/对比 主DTB+overlay） |
| `dtb_diff.py` | 节点级全量 DTB diff 工具 |
| `dtb_dump.py` | 纯 Python DTB→DTS 反编译器（无 dtc 依赖） |
| `diff_main_oem.txt` / `diff_dtbo_oem.txt` | 主 DTB / overlay 的全量 diff 报告 |

可复现抽取（macOS 即可跑）：
```sh
python3 - <<'PY'
# 主 DTB：boot.bin 里 kernel 区末尾的 appended DTB
import struct
def grab(boot, out):
    d=open(boot,"rb").read(); ks=struct.unpack("<I",d[8:12])[0]
    reg=d[2048:2048+ks]; pos=reg.rfind(b'\xd0\x0d\xfe\xed')
    ts=struct.unpack(">I",reg[pos+4:pos+8])[0]
    open(out,"wb").write(reg[pos:pos+ts])
grab("柴提全原无数据4G_9.1864/4G原包/boot.bin","port/4g_oem_boot.dtb")
grab("柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/boot.bin","port/wifi_stock.dtb")
# overlay：dtbo.bin 里第一个 DTB magic
def grab_dtbo(dtbo,out):
    d=open(dtbo,"rb").read(); i=d.find(b'\xd0\x0d\fe\xed')
    ts=struct.unpack(">I",d[i+4:i+8])[0]; open(out,"wb").write(d[i:i+ts])
grab_dtbo("柴提全原无数据4G_9.1864/4G原包/dtbo.bin","port/4g_oem_dtbo.dtb")
grab_dtbo("柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/dtbo.bin","port/dtbo_internal.dtb")
PY
python3 dtb_dump.py port/wifi_stock.dtb port/wifi_stock.dts
python3 dtb_dump.py port/dtbo_internal.dtb port/dtbo_internal.dts
```

---

## 5. 构建 / 打包 / 刷机 Runbook

> 需在 **Linux 构建机**执行（macOS 缺 `aarch64-linux-gnu-gcc` 与设备，无法真编真刷）。
> 构建机装好：`aarch64-linux-gnu-gcc/binutils`、`make`、`python3`。

### 5.1 构建并打包（Stage 2 正规流程）
```sh
cd /path/to/Archytas_64/port
chmod +x build_archytas_wifi.sh
./build_archytas_wifi.sh          # 产出 port/boot-archytas-wifi.img（32MiB）
```

### 5.2 仅做 Stage 1（4G 模板直刷，最快）
```sh
cd /path/to/archimedes-kernel-64-main
JOBS=24 ./build_archimedes.sh pure    # 产出 out-archimedes-pure/.../Image.gz-dtb
python3 package_archimedes_boot.py \
  --template tools/archimedes-boot-template-32MiB.img \
  --kernel out-archimedes-pure/arch/arm64/boot/Image.gz-dtb \
  --output boot-archytas-wifi-stage1.img
```

### 5.3 刷入（MTKClient；ROM 内 txt 注明密码 123456）
```sh
mtk payload
mtk partition getboot        # 确认 boot / dtbo / vbmeta 分区名
# ⚠️ 先备份原分区（含 vbmeta！）
mtk r boot boot_bak.img
mtk r dtbo dtbo_bak.img
mtk r vbmeta vbmeta_bak.img
# 刷入：boot 用移植产物，dtbo【必须】用 Wi-Fi 原厂
mtk w boot boot-archytas-wifi.img          # 或 stage1 镜像
mtk w dtbo "../柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/dtbo.bin"
# ⚠️ AVB：archimedes boot 无 AVB 哈希，与 Wi-Fi 原厂 vbmeta 记录的哈希不匹配会校验失败拒启。
#    方案A（推荐）：禁用验证后刷回原厂 vbmeta
mtk w vbmeta --disable-verification
#    方案B：保留验证则需用 archimedes 私钥重签 vbmeta（通常无私钥）→ 用方案A
mtk reset
```
> **dtbo 分区必须用 Wi-Fi 原厂的**，不要用 4G 原厂或 archimedes 自带（若有）——否则会带入 SIM GPIO 与错误的按键配置。
> **vbmeta 必须处理**：见第 6 节 AVB 风险。

---

## 6. 风险与回退

- **回退**：保留 `boot_bak.img` / `dtbo_bak.img` / `vbmeta_bak.img`，重新 `mtk w boot/dtbo/vbmeta <备份>` 即可恢复。
- **AVB 验证启动（最关键风险）**：Wi-Fi 原厂启用 AVB（`boot` 含 `AVB0` 哈希描述符 + 有 `vbmeta.bin`）。archimedes boot 无 AVB 哈希，与 Wi-Fi 原厂 `vbmeta` 记录的哈希不匹配 → 启动时 AVB 校验失败，可能**拒绝启动 / 橙屏警告**。解决：刷 archimedes boot 的同时，用 `mtk w vbmeta --disable-verification` 禁用验证（或刷关闭验证的 `vbmeta` 镜像）。禁用验证仅影响启动校验、不破坏 `system` 数据；要恢复验证刷回 `vbmeta_bak.img` 即可。
- **基带 vs Wi-Fi 版**：设备树里基带（CCCI/MD1）节点 4G 与 Wi-Fi **完全一致且都在**，但 Wi-Fi 版 overlay **无 SIM GPIO**（硬件无 SIM 卡槽）。若 4G 内核初始化 Modem 失败刷日志，可用 defconfig 关 CCCI/MD1 变纯 Wi-Fi；CONSYS 的 Wi-Fi/BT/GPS 保持开启。
- **LCM 面板名**：由 `lk` 经 atag 传入内核，不在 DTB。黑屏但能 adb → 多半 lk 传的面板名与内核 `CONFIG_CUSTOM_KERNEL_LCM` 列表不匹配；沿用 Wi-Fi 原厂 `lk.bin` 即可（它本就对应这块屏）。
- **触控 / 音频 / 充电 / 相机 / LED / 振动 / NFC / USB-C**：双方 overlay 设备节点集合完全相同，风险低。仅 `charger` 限流参数 archimedes 编译版与原厂略有不同（无害）。
- **按键**：`fragment@39/@49` 的 debounce/中断参数双方不同——务必用 Wi-Fi 原厂 dtbo 以保证按键正确。

---

## 7. 待真机验证清单

- [ ] Stage 1 的 4G 模板镜像 + Wi-Fi dtbo 能否启动到 Android（确认同根）
- [ ] 显示 / 触控 / 声音 / Wi-Fi / 蓝牙 / 充电是否正常
- [ ] 物理按键（电源/音量）是否正常（验证 dtbo 是否为 Wi-Fi 原厂）
- [ ] 是否需要关基带（看内核日志是否有 CCCI/MD1 报错）
- [ ] LCM 面板是否点亮（黑屏则查 lk atag 与 LCM 驱动列表）

---

*生成/修订于 2026-10-02。工具链：自写 `dtb_dump.py`（反编译）、`dtb_diff.py`（全量 diff）、`make_wifi_template.py`（DTB 替换）、`analyze_oem_diff.py`（原厂对比）。权威证据：4G/ Wi-Fi 双方原厂 `boot.bin`+`dtbo.bin` 抽出的 DTB 与 `diff_main_oem.txt`/`diff_dtbo_oem.txt`。*
