# 小爱老师 Wi-Fi 版（Archytas）内核移植方案

> 把 `archimedes-kernel-64-main`（小爱老师 4G 版 64 位内核，Linux 4.9.117 / MT6761）
> 移植到 **Wi-Fi 版** 硬件。
> - 4G 版完整 ROM：`柴提全原无数据4G_9.1864/4G原包/`
> - Wi-Fi 版完整 ROM：`柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/`

---

## 0. 修订记录（重要：之前结论已修正）

本文档早期版本基于"archimedes 4G 模板 DTB vs Wi-Fi 原厂主 DTB"的抽样对比，得出了**错误**的中间结论（"Wi-Fi 版关了 msdc2/3/dsi_te/rt9465_slave_chr""板级差异在 overlay 全节点"）。
拿到 **4G 原厂 ROM** 后，做了**原厂对原厂**的权威对比，结论已彻底修正，见第 1 节。

**2026-10-04 追加修正（见第 8 节）**：连通性模块 `insmod` 报 `unsupported RELA relocation: 275`，
曾两次误判为「`-mcmodel=large` 没进编译命令行」（先后归因于 `KCFLAGS`、`ccflags-y`）。
实际读 `.ko` 内嵌 `DW_AT_producer` 证明该开关**一直都在**；`-mcmodel=large` 本就消除不了这些
指向 `.text` 函数内字面量池的 ADRP。真正原因在 `arch/arm64/kernel/module.c` 的
`#ifndef CONFIG_ARM64_ERRATUM_843419` 守卫，已按第 8.3 节手术式修复。

**2026-10-04 深夜追加（见第 9.11 节）**：Wi-Fi 模块加载三连胜但 `wlan0` 永不出现，
FW_START 握手超时 + `gRxCount=-1` 垃圾值。根因：`consys-reserve-memory` 用 `alloc-ranges`
动态分配地址，而 bootloader/WMT 硬编码 CONSYS EMI 窗口在 `0xbf000000` → STP 共享内存
两侧地址不匹配。修法：`patch_wifi_dtb.py` 扩展（零尺寸增长），把 `alloc-ranges` 换成固定
`reg = <0x0 0xbf000000 0x0 0x400000>`。

**2026-10-05 追加（见第 9.13/9.15 节）**：consys DTB 修复后 FW_START EVT success 但
固件 crash at EVA=0xF0400000 → `CFG_MTK_ANDROID_EMI=0` 让 EMI 固件段被静默跳过。
`wlanDownloadEMISection` 在旧 `.ko` 里是 8 字节 stub（MOV X0,0; RET）。
修法：`build_connectivity_modules.sh` 的 `build_mod wlan` 加 `MTK_ANDROID_EMI=y`
（wlan/core Makefile 自己有 `ifeq ($(MTK_ANDROID_EMI), y)` 控制分支，之前只传了
`MTK_ANDROID_WMT=y`）。修完后 `wlanDownloadEMISection` 从 8 字节长到 556 字节。

**2026-10-05 里程碑（见第 9.15 节）**：Wi-Fi **完全通了**——FW_START EVT success、
main_thread 正常运行、扫描 7 个 AP、关联成功、拿到 IP 192.168.1.41、ping 通网关
192.168.1.1 零丢包。原厂 init.rc 自动加载 64 位 .ko 生效（覆盖 `/vendor/lib/modules/`
下的 32 位版本），每次重启自动工作。累计 6 个根因。

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

## 8. 连通性内核模块（64 位 `.ko`）— 2026-10-04

### 8.1 为什么必须重编

原厂 `/vendor/lib/modules` 下的连通性模块是 **32 位 ARMv7**，`insmod` 进 64 位内核必然
`Exec format error`。因此 Wi-Fi/BT 要在 64 位内核上可用，必须自建 4 个 **AArch64** 模块：

| 目标文件名 | 来源仓库 @ commit | 说明 |
| --- | --- | --- |
| `wmt_drv.ko` | MotorolaMobilityLLC `…connectivity-common` @ `364afcf` | WMT 传输层 |
| `wmt_chrdev_wifi.ko` | MotorolaMobilityLLC `…connectivity-wlan-adaptor` @ `e84d47e` | WLAN 字符设备 |
| `wlan_drv_gen4m.ko` | zainarbani `…wlan-core-gen4m` @ `ba2c5a5` | **真实名 `wlan_mt6761_axi.ko`**（`wlan_<chipid>_<hif>` 派生），按原厂命名改名 |
| `bt_drv.ko` | MotorolaMobilityLLC `…connectivity-bt-mt66xx` @ `9074126` | 蓝牙 |

构建脚本：**`port/build_connectivity_modules.sh`**；真机安装：`port/install_wifi_modules.sh`。
加载顺序（原厂 `/vendor/etc/init/*.rc`）：`wmt_drv` → `wmt_chrdev_wifi` + `wlan_drv_${ro.vendor.wlan.gen}` + `bt_drv`。

### 8.2 ADRP 重定位：真正的病根（此前两次误判）

现象：`insmod` 报 **`unsupported RELA relocation: 275`** + `Exec format error`。

| 结论 | 证据 |
| --- | --- |
| ① `-mcmodel=large` **本来就已经生效** | 直接读 `.ko` 内嵌的 `DW_AT_producer`：`GNU C89 11.4.0 … -mcmodel=large -mabi=lp64 … -fno-pic -fstack-protector-strong`。它由 `CONFIG_ARM64_MODULE_CMODEL_LARGE`（被 `ARM64_ERRATUM_843419` 自动 `select`）经 `arch/arm64/Makefile:81` 的 `KBUILD_CFLAGS_MODULE` 注入 |
| ② 之前怀疑「KCFLAGS / ccflags-y 没进 cc1」是**错的** | 同一编译前后 `.ko` 字节几乎一致，是因为**该开关压根不消除这些 ADRP** |
| ③ 这 161 个 ADRP 的真实身份 | 全部指向 **`.text` 节符号本身**（`st_name==0`），addend 小而 8 字节对齐，且**紧跟在每个函数体之后**（如 `BT_poll@0x100+0xd4` → 池在 `0x1d8`），即 GAS 打 `$d` 标记的**函数内字面量池**；成对的 64 位槽由 `R_AARCH64_ABS64(257)` 填充 |
| ④ 内核为何拒收 | `arch/arm64/kernel/module.c:334` 用 `#ifndef CONFIG_ARM64_ERRATUM_843419` 把 `R_AARCH64_ADR_PREL_PG_HI21(_NC)`（275/276）的处理器**整体编译掉了**（上游设计前提是「大代码模型的模块不含 ADRP」，对这批老 MTK 模块不成立） |
| ⑤ 相关但非主因 | binutils aarch64 链接器**默认开启** `--fix-cortex-a53-843419`；`arch/arm64/Makefile` 只在「erratum 关闭」分支加 `--no-fix-…`，所以 erratum 开启时模块链接仍做该修补 |

### 8.3 修复（脚本 step 2e，幂等）

改**内核侧**（唯一真正决定权的地方）：去掉 `module.c` 里那对守卫，让 ADRP 重定位正常处理。

- **没有**选「defconfig 关 `CONFIG_ARM64_ERRATUM_843419`」——那会同时把
  `--fix-cortex-a53-843419` 从 `LDFLAGS_vmlinux` 拿掉，等于**连内核自身的 erratum 规避都放弃**
  （Cortex-A53 真实硬件缺陷：load/store 可能访问错误地址）。手术式补丁保留内核侧规避，只放宽模块加载策略。
- 打补丁后 `grep ARCHYTAS_ADRP_RELOC arch/arm64/kernel/module.c` 必须命中；脚本以此作为**硬门禁**。
- **补丁实现独立成 `port/patch_adrp_reloc.py`（幂等），且必须在编译内核之前执行**：
  `module.c` 是**编进内核本体**的，若在内核编完之后才打补丁（早期版本就是把它放在模块步骤里），
  artifact 里的 boot 镜像仍是不带补丁的 loader，`.ko` 照样 `insmod` 失败 —— 而且**构建是全绿的，属于静默错误**。
  workflow 第 4 步（`Patch kernel so the module loader handles ADRP relocations`）排在 `Build kernel` 之前；
  模块脚本里的那次调用只是「只编模块」模式与源码一致性的兜底。

> ⚠️ **副作用**：改的是内核，所以**必须重新编译并重刷一次 boot 镜像**。此后迭代 `.ko` 用下面的快车道即可。

### 8.4 快车道：只编模块（workflow_dispatch → `modules_only=true`）

慢的一直是 `Image.gz-dtb`（~7–10 min），而脚本本身**从不**编内核。快车道 = 跳过内核步骤，
由脚本自己 `defconfig`（+关 KernelSU）`+ modules_prepare`：

| 模式 | 做什么 | 耗时 | 产物 |
| --- | --- | --- | --- |
| 默认（full） | 内核 `Image.gz-dtb` + 4 个 `.ko` + 打包 boot | ~11 min | `archytas-wifi-boot-and-modules`（含 boot 镜像） |
| `modules_only=true` | 仅 4 个 `.ko`（自动 `modules_prepare`） | ~3 min | `archytas-wifi-modules-only`（仅 `.ko`） |

改动只在 **`.github/workflows/build.yml`**（`build_stage` 之上的 `modules_only` 输入 + 条件跳过内核/打包/上传）。

产物自检（每轮都打印，便于一轮定位）：

- `report_relocs()`：每个 `.ko` 的 ADRP(275/276) 计数 + 从 `DW_AT_producer` 读出的实际 codegen 开关
- `modinfo` 段：内部 `name=`、`vermagic=`（本机为 `4.9.117+ SMP preempt mod_unload modversions aarch64`）、`__versions` 条数
- 断四件套齐全 + 内核补丁存在

> 注：`vermagic` 含 `modversions`（`CONFIG_MODVERSIONS=y`）。快车道没有内核 `Module.symvers`，
> 所以 `__versions` 会是 **0 条**——`check_version()` 对「未列出的符号」放行（走到
> “broken toolchain, warn once, return 1” 分支），**仍可 insmod**；只有「列出但 CRC 不符」或
> 「整个 `__versions` 段缺失」才会失败。modpost 的未定义符号报错也已被
> `KBUILD_MODPOST_FAIL_ON_WARNINGS=`（`scripts/Makefile.modpost:81` 据此加 `-w`）压掉。

### 8.5 收集顺序：`rm -rf` 把子模块吃掉了（run #37189514054）

第四个坑，也是**我自己引入的**，而且是最隐蔽的一个：日志显示四个模块**全部链接成功**，
脚本最后仍然 `ERROR: missing connectivity modules: wmt_chrdev_wifi.ko`。

时间线（同一份日志）：

| 时刻 | 事件 |
| --- | --- |
| 08:47:34.44 | `wmt_chrdev_wifi.ko` 的 MODPOST 未定义符号警告（说明它被链接了） |
| 08:47:34.59 | 进入 `build_mod wlan` —— 该函数第一件事是 `rm -rf "$KOUT/$CONN/wlan"` |
| 08:48:14.45 | `find "$KOUT" -name '*.ko'` 只剩 **5** 个：`connectivity-modules/{bt_drv,wmt_drv}.ko`、`.../bt/bt_drv.ko`、`.../common/wmt_drv.ko`、`.../wlan/wlan_mt6761_axi.ko` |
| 08:48:14.56 | `ERROR: missing connectivity modules: wmt_chrdev_wifi.ko` |

根因：`wlan/adaptor` **是 `wlan` 的子目录**，而 `build_mod()` 为了强制丢弃陈旧 `.o`
（防止 `-mcmodel=large` 变更被缓存掩盖）会 `rm -rf "$KOUT/$CONN/$d"`。
原来调用顺序是 `common → wlan/adaptor → bt → wlan`，最后那次 `build_mod wlan`
把第 2 步刚编好、还没来得及收集的 `adaptor/wmt_chrdev_wifi.ko` 连锅端掉；
随后重建的 `wlan/` 里不再有 `adaptor/`，而末尾的 `find` 扫描自然什么也找不到。

> 也就是说：**这一步不是"没编出来"，是"编出来了又被自己删了"**。日志里
> `LD [M] …/wmt_chrdev_wifi.ko` 存在、而 41 秒后文件不存在 —— 只看构建输出永远查不出来。

修复（两层，缺一不可）：

1. **编完立刻收集**：新增 `collect_mod()`，在每个 `build_mod` 之后立即把该目录下的 `.ko`
   拷进 `$OUTDIR`（`$KOUT/connectivity-modules`，**位于 `$KOUT/$CONN` 之外**，不在任何 wipe 范围内）。
   结果与构建顺序**解耦**。
2. **父目录先于子目录**：调用顺序改为 `common → wlan → wlan/adaptor → bt`，
   并在 `build_mod()` 的 `rm -rf` 上方写明 HAZARD。

顺带修掉两个同源隐患：

- `$OUTDIR` 在构建前 `rm -rf` + 重建，避免**上次本地残留的 `.ko`** 让"四件套齐全"断言假通过。
  并加了一道 `case "$OUTDIR" in "$KOUT"/*)` 的护栏，拒绝清 `$KOUT` 之外的路径。
- 末尾识别 wlan-core 的 `find | head -1` 换成 **glob 循环**（同一个 pipefail/SIGPIPE 陷阱），
  并显式跳过已重命名的 `wlan_drv_gen4m.ko`，避免把上一轮的产物当成新编的模块。

### 8.6 体积：文件大小 ≠ 内存占用（顺手修掉 57 MiB 重复）

run #37190391324 全绿后，产物里出现两个问题：`wlan_mt6761_axi.ko` 与 `wlan_drv_gen4m.ko`
是**同一份 57 MiB 文件的两份拷贝**（重命名时没删原件）；以及 `.ko` 文件大得离谱。

用 `port/elf_debug_share.py` 量了一遍（能同时解析 64 位自编模块与 32 位原厂模块）：

| 模块 | 原厂 32 位 文件 | 我们 64 位 文件 | 我们 `strip -g` 后 | **ALLOC（insmod 真正映射）** |
| --- | --- | --- | --- | --- |
| `bt_drv.ko` | 25,888（已 strip） | 461,496 | 240,193 | 21,878 vs 原厂 19,552 |
| `wmt_chrdev_wifi.ko` | 23,592（已 strip） | 686,368 | 342,804 | 17,241 vs 原厂 13,358 |
| `wmt_drv.ko` | 874,268（已 strip） | 8,879,904 | 5,072,216 | 1,135,019 vs 原厂 963,846 |
| `wlan_drv_gen4m.ko` | 2,046,580（已 strip） | 57,129,152 | ~29,000,000 | — |

三条结论：

1. **原厂模块本来就是 strip 过的** —— 七个原厂 `.ko` 全部报 `strip -g removes 0 bytes`。
   我们这批带 `-g`（defconfig 里 `CONFIG_DEBUG_INFO`），debug 节占 **43–50%**。
   已加 step 5b（`strip -g`，等同 Kbuild 的 `INSTALL_MOD_STRIP=1`）。
2. **文件大小不是 insmod 的障碍**：内核只映射 `SHF_ALLOC` 节，我们的 ALLOC 只有
   17 KiB / 21 KiB / 1.08 MiB，与原厂 32 位模块（13 / 19 / 941 KiB）同一量级
   （差距就是 64 位指针的自然膨胀）。57 MiB 只是 **adb push 与分区空间的成本**。
3. 为避免"strip 静默失败"或"strip 弄坏模块"两种静默故障，做了双向自证：
   - `strip` 后**重新**跑 `.modinfo` 提取（`.modinfo` / `__versions` 必须仍可读）；
   - 若某个 `.ko` strip 后**没有变小**，打印 `WARN`。
   - `report_relocs()` 有意排在 strip **之前**（`DW_AT_producer` 在 `.debug_info` 里，strip 后就没了）；
     `strip -g` 不动 `.rela.*`，所以那时打印的 ADRP 计数仍然描述所交付的文件。

`port/verify_build_script.py` 第 6 组同步加了三条结构断言：重命名后必须删掉原件、
profile 必须在 strip 之前、`.modinfo` 必须在 strip 之后。

### 8.7 本地校验工具

- `port/verify_build_script.py`：改脚本后**先跑它**。含 7 组检查：`bash -n`；
  **禁止无兜底的 `| head` 管道**（脚本是 `set -euo pipefail`，`find|head` 的 SIGPIPE 141 会被当成致命失败 ——
  这正是 run #37186797793 在第一个模块后静默 exit 1 的原因）；逐个内嵌 python 的 AST 校验；
  用真实内核源码跑 `patch_adrp_reloc.py`（含幂等 + 反向用例）；**校验 workflow 里补丁步骤排在内核构建之前**；
  **校验模块构建顺序 + 立即收集不变量**（父目录先于子目录、每个 `build_mod` 后紧跟自己的 `collect_mod`、
  `OUTDIR` 在首次构建前被清理）；用真实 `.ko` 跑 `.modinfo` 提取器。
  支持 `python3 port/verify_build_script.py <别的脚本副本>` 做**反向验证**——
  例如 `git show HEAD:port/build_connectivity_modules.sh > /tmp/old.sh` 再跑，应当报出
  `wlan built after wlan/adaptor`，据此确认检查本身有效。
  用真实 `.ko` 跑 `.modinfo` 提取器。
- `port/ci_logs.py`：直接拉 GitHub Actions 日志（凭据取自 git credential manager，不打印）。
  `--list` 列 run；默认取最近一个失败 run，打印失败步骤名 + 日志尾部 + 关键行命中。
  **以后 CI 失败先跑它，不要靠猜。**
- `port/dump_relocs.py <ko> [--targets]`：AArch64 重定位统计；`--targets` 按 `st_shndx` 解析节符号
  （判断 ADRP 指向哪一节，是定位本问题的关键）。

## 9. 真机结果：ADRP 修复已被证实，卡在 WMT probe 崩溃（2026-10-04 17:2x）

第一次把 CI 产物真正刷进设备并加载模块。**方法上有个重要变化：不再需要 BROM/短接。**

### 9.1 刷机路径：用 root + dd 直接写 boot（比 MTKClient 安全得多）

设备上 `adbd` 已经是 root（`adb shell id` → `uid=0`；重启后会降回 shell，`adb root` 可恢复），
于是不必进下载模式、不用短接 test point：

| 步骤 | 命令要点 |
| --- | --- |
| 备份（强制） | `dd if=/dev/block/by-name/boot of=/data/local/tmp/bak/…` + `adb pull` |
| 写入 | `dd if=/data/local/tmp/newboot.img of=/dev/block/by-name/boot bs=1M` |
| 回读校验 | `dd if=/dev/block/by-name/boot bs=1M \| sha256sum` 必须等于**待刷镜像的 sha256** |
| 重启 | `adb reboot` |

分区尺寸正好等于镜像：`boot` = mmcblk0p25 = 32 MiB，`dtbo` = mmcblk0p27 = 8 MiB。
本次记录：备份 `78db70e2…`（Oct 2 内核）、待刷 `3060785b…`（Oct 4 内核）→ 回读一致。
**`dtbo` 未动**（设备仍是 Wi-Fi 原厂 dtbo），`vbmeta` 未动（当前自定义 boot 本来就能启，说明 AVB 已不拦）。

### 9.2 结果：ADRP 修复生效 ✅

刷后 `uname` 变成 `Sun Oct 4 08:58:54 UTC 2026`（新内核）。同一个模块、同一台设备：

| | 旧内核（Oct 2 23:38） | 新内核（Oct 4 08:58） |
| --- | --- | --- |
| `insmod wmt_drv.ko`（9014 个 ADRP） | `Exec format error`，dmesg：**`unsupported RELA relocation: 275`** | **rc=0** ✅ |
| `insmod wmt_chrdev_wifi.ko`（197 个 ADRP） | `No such file or directory`（符号未解析） | **rc=0** ✅ |

这正面证伪了"`-mcmodel=large` 没生效"的旧诊断，也证实 `patch_adrp_reloc.py` 的内核侧修复是对的。

> 顺带一个坑：`insmod` 必须以 root 跑。重启后 adbd 默认降为 shell，此时任何 `insmod` 都返回
> `Operation not permitted`(EPERM) —— 这不是内核策略拦的，只是缺 `CAP_SYS_MODULE`。
> 差点把它误读成"模块签名校验"。

### 9.3 新阻塞：`wmt_drv` 的 `mtk_wmt_probe` 空指针 → kernel panic ❌

加载 `wlan_drv_gen4m.ko` 时 adb 断开。查 `console-ramoops`（`androidboot.bootreason=kernel_panic`）：

```
[327.976602] mtk_wcn_get_consys_ic_ops:Does not support on combo          <- 关键前兆
[327.976612] wmt_export_platform_bridge_register:
[327.978507] Internal error: Accessing user space memory outside uaccess.h routines: 96000005
             PC is at mtk_wmt_probe+0x40/0x400 [wmt_drv]
             LR is at platform_drv_probe+0x58/0xd0
             Modules linked in: wmt_chrdev_wifi … wmt_drv …
```

调用链（由内向外）：
`WMT_compat_detect_ioctl` → `wmt_detect_unlocked_ioctl` → `mtk_wcn_common_drv_init` →
`wmt_lib_init` → `wmt_plat_init` → `mtk_wcn_consys_hw_init` → `__platform_driver_register` →
`driver_attach` → `platform_drv_probe` → **`mtk_wmt_probe`**。

触发者是**厂商用户态 `wmt_loader`**（32 位），它在 `wmt_drv` 的字符设备出现后立刻下发 ioctl，
这是原厂正常流程 —— 也就是说这条路径一到就会崩。

判读要点：

- ESR `96000005` = EL1 数据异常、level-1 translation fault。
- 内核把**任何 < TASK_SIZE 的故障地址**都报成 “Accessing user space memory outside uaccess.h routines”，
  所以这条消息**不等于**"32 位 ABI 问题"，NULL 附近的小偏移也会这样报。崩溃时 `x0=x1=x2=0`。
- **已确认根因**（见 9.5）：`mtk_wcn_get_consys_ic_ops()` 返回 NULL（"Does not support on combo"），
  `mtk_wmt_probe` 未判空即解引用。
- **已排除**：DT 缺节点。运行时 `/sys/firmware/devices/18002000.consys` 存在，
  DTB 里 `consys@18002000` 完整（`compatible = "mediatek,mt6761-consys"`，11 段 `reg`、`clocks`、`wifi@18000000` 都在）。

### 9.4 设备当前状态与风险

- 设备现在跑新内核，**启动正常、稳定**（uptime 持续增长，`lsmod` 为空，`wmt_loader`/`wmt_launcher` 在 nanosleep 等待）。
- Wi-Fi 仍然不通 —— 与刷机前一致，没有回退化。**新内核严格更优**（是它让 64 位模块变得可加载），所以**不需要回滚**。
- ⚠️ **不要把 64 位 `.ko` 拷进 `/vendor/lib/modules/`**：原厂 init 会在开机时 `insmod` 它们，
  从而必然触发上面的 panic → **开机循环**。目前只从 `/data/local/tmp/wifi64` 手动 `insmod`，不持久化。
- 回滚路径：`dd if=port/_device_backup/bak_before_adrp/bak_before_adrp/boot.img of=/dev/block/by-name/boot bs=1M`。
- 崩溃日志已归档：`port/_device_backup/panic_20261004/console-ramoops.txt`。

### 9.5 Wi-Fi 不通的真因：`CONFIG_MTK_COMBO_CHIP` 的引号被抹掉了 ✅ 已修

**一句话**：`common/Makefile:198` 用**带引号**的模式判断芯片选型，而我们的构建把值里的引号抹掉后在
make 命令行覆盖 —— 于是 `common_main/platform/mt6761.o`（WMT IC-ops 的真正提供者）**从未被编译**。

#### 病根：Kconfig 字符串值自带引号

```
# drivers/misc/mediatek/connectivity/Kconfig:128
config MTK_COMBO_CHIP
        string
        default "CONSYS_6761" if MTK_COMBO_CHIP_CONSYS_6761     # defconfig 里是 y
```

Kconfig 写字符串值**保留引号**，所以 `.config`/`auto.conf` 里是
`CONFIG_MTK_COMBO_CHIP="CONSYS_6761"`，make 展开得到 **12 个字符**的词 `"CONSYS_6761"`——
注意**含双引号**。而模块 Makefile 的判断也带引号，两边是**配套设计**的：

```make
ifneq ($(filter "CONSYS_%",$(CONFIG_MTK_COMBO_CHIP)),)          # 198  模式含引号
$(MODULE_NAME)-objs += common_main/platform/$(MTK_PLATFORM).o   # 199  芯片文件
endif
ifneq ($(filter "CONSYS_%",$(CONFIG_MTK_COMBO_CHIP)),)          # 113  → -D MTK_WCN_SOC_CHIP_SUPPORT
ifneq ($(filter "CONSYS_%",$(CONFIG_MTK_COMBO_CHIP)),)          # 139  → -D MTK_WCN_WLAN_GEN2
```

旧脚本传的是 `CONFIG_MTK_COMBO_CHIP=CONSYS_6761`（**没引号**），而
**make 命令行变量优先级高于 auto.conf**，于是覆盖掉了带引号的原值，
`$(filter "CONSYS_%", CONSYS_6761)` 恒为空 —— 上面三处**同时失效**。

#### 后果：`mtk_wmt_probe` 空指针

`mt6761.c:1072` 的强符号没了，链接保留了 `mtk_wcn_consys_hw.c:149` 的弱存根：

```c
P_WMT_CONSYS_IC_OPS __weak mtk_wcn_get_consys_ic_ops(VOID)
{ WMT_PLAT_PR_WARN("Does not support on combo\n"); return NULL; }
```

→ `wmt_consys_ic_ops` 恒为 NULL → `mtk_wmt_probe()` 第 166 行 `wmt_consys_ic_ops->consys_ic_need_store_pdev`
（偏移 0）直接解引用 → panic（ESR `96000005`，`x0=x1=x2=0`）。前兆日志
`mtk_wcn_get_consys_ic_ops:Does not support on combo` 就是那个弱存根打的。

#### 铁证：符号表（不是字符串计数）

**坑**：`strings | grep "Does not support on combo"` 在**原厂模块里也命中 1 次**，
因为模块链接是 `ld -r`，被覆盖的弱函数体会作为死代码留在 `.text`、其日志字符串留在 `.rodata`。
**只有符号表能区分**（新工具 `port/elf_symbols.py`，同时支持 ELF32/64）：

| 符号 | 原厂 32 位 `wmt_drv.ko` | 我们（修复前） |
| --- | --- | --- |
| `mtk_wcn_get_consys_ic_ops` | **GLOBAL FUNC，24 B**（真实现 `return &consys_ic_ops`） | **WEAK FUNC，68 B**（NULL 存根） |
| `consys_ic_ops`（172 B ops 表） | **GLOBAL OBJECT，存在** | **完全缺失** |
| `mtk_wcn_consys_jtag_flag_ctrl` | GLOBAL，104 B | WEAK，68 B |

#### 修法

1. **不再凭空造值**：从内核自己的 `auto.conf`/`.config` 读出 `CONFIG_MTK_COMBO_CHIP`，
   **保留引号**（`COMBO_CHIP_MAKE="\"$COMBO_CHIP\""`）；读不到才回落 `CONSYS_6761` 并告警。
2. `MTK_PLATFORM` 同样从 `CONFIG_MTK_PLATFORM` 去引号得出 + 显式传给 `build_mod common`。
   （正常情况下它是白送的：`arch/arm64/Makefile:138-140` 已经 `MTK_PLATFORM := $(CONFIG_MTK_PLATFORM:"%"=%)`
   并 `export`；顶层 `Makefile:648` 的 `include arch/$(SRCARCH)/Makefile` 在 `KBUILD_EXTMOD` 分支**之外**，
   所以 `M=` 构建里也生效。显式再断言一次是防回退。）
3. **两道硬门禁**（任一不过就 FAIL，不让坏模块出厂）：
   - 门禁 1：`$KOUT/.../common/common_main/platform/mt6761.o` 必须存在（紧跟 `build_mod common` 之后）。
   - 门禁 2：`wmt_drv.ko` 的符号表里 `mtk_wcn_get_consys_ic_ops` 必须是 **GLOBAL**（不是 WEAK）、
     且 `consys_ic_ops` 必须存在；读的是 **`strip -g` 之后**的字节，即真正推给设备的那个文件。
4. 自检：`port/verify_build_script.py` 新增第 8 组（含**反向对照**：拿修复前的脚本跑会精确报出 9 条失败）。

#### 顺带确认的两件事

- `MTK_PLATFORM=mt6761` 下会多激活一批 `-I` 路径，均已核对在本内核树中存在：
  `mt-plat/mt6761/include{,/mach}`、`base/power/mt6761`、`base/power/include/clkbuf_v1/mt6761`
  （`mtk_clkbuf_hw.h` **只**在这个子目录里，也是必须带 `MTK_PLATFORM` 的另一个理由）、
  `eccci/mt6761`、`emi/mt6761`。
  不存在的 `mach/mt6761/include/mach`、`arch/arm/mach-mt6761`（`Makefile:49/93`）只是落空的 `-I`，
  GCC 静默忽略，不报错。
- `mt6761.c` 的 `consys_ic_ops` 初始化表**没有**赋 `.consys_ic_need_store_pdev`，
  即该成员为 NULL —— 所以修复后 `mtk_wmt_probe` 的 `if (wmt_consys_ic_ops->consys_ic_need_store_pdev)`
  会走 false 分支，不崩。
- `wlan/adaptor`、`bt-mt66xx` 两仓**完全不引用** `CONFIG_MTK_COMBO_CHIP`/`MTK_PLATFORM`，
  给它们传值无害但无用；`wlan core` 用 `$(CONFIG_MTK_PLATFORM)`（来自 auto.conf，不受影响）。

### 9.6 第二个根因：wlan 芯片层整块没编（`MTK_COMBO_CHIP` 要传**家族名**）✅ 已修

修完 9.5 后，`wmt_drv.ko` / `wmt_chrdev_wifi.ko` 加载成功、`mtk_wmt_probe` 完整跑通（真机证据见 9.7）。
但加载 `wlan_drv_gen4m.ko` **仍然 panic**，只是崩溃点换了地方：

```
PC is at mtk_axi_probe+0x2c/0x48 [wlan_mt6761_axi]
LR is at mtk_axi_probe+0x20/0x48 [wlan_mt6761_axi]      x1 : 0        <- NULL 解引用
```

#### 病根：`mtk_axi_probe` 从 `mtk_axi_ids[]` 取芯片数据，而该字段受 `#ifdef CONNAC` 控制

```c
/* os/linux/hif/axi/axi.c:101 */
static const struct platform_device_id mtk_axi_ids[] = {
    { .name = "CONNAC",
#ifdef CONNAC
      .driver_data = (kernel_ulong_t)&mt66xx_driver_data_connac },
#endif
#ifdef CONNAC2X2
      .driver_data = (kernel_ulong_t)&mt66xx_driver_data_connac2x2 },
#endif
#ifdef SOC3_0
      .driver_data = (kernel_ulong_t)&mt66xx_driver_data_soc3_0 },
#endif
    { /* end: all zeroes */ },
};

/* axi.c:744 mtk_axi_probe */
prDriverData = (struct mt66xx_hif_driver_data *) mtk_axi_ids[0].driver_data;   /* -> NULL */
prChipInfo   = prDriverData->chip_info;                                        /* -> 崩 */
```

`driver_data` 只在 `CONNAC`/`CONNAC2X2`/`SOC3_0` 被定义时才有值，而这三个宏由
`wlan/Makefile` 从 **`MTK_COMBO_CHIP` 里的家族词**推出：

```make
ifneq ($(filter CONNAC,$(MTK_COMBO_CHIP)),)        # 101
ccflags-y += $(filter-out -UCONNAC,$(ccflags-y))
ccflags-y += -DCONNAC
endif
...
ifneq ($(filter CONNAC,$(MTK_COMBO_CHIP)),)        # 590
CHIPS_OBJS += $(CHIPS)connac/connac.o             # 592  <- 整层芯片数据
endif
```

我们传的是 `MTK_COMBO_CHIP=MT6761` —— **零件号**，三个 `filter` 全不命中 ⇒
`chips/connac/connac.o` 从未编译 ⇒ `mtk_axi_ids[0].driver_data = 0` ⇒ 首次 probe 就崩。

**最阴的地方**：`CHIPS_OBJS += cmm_asic_connac.o` 与 `dbg_connac.o`（第 566–567 行）
是**无条件**加的，所以模块看上去"有 connac 代码"，编译链接全绿、门禁也可能被骗过；
真正缺的是**芯片数据**本身 —— `connac.c:385` 的
`mt66xx_driver_data_connac = { .chip_info = &mt66xx_chip_info_connac }`。

#### 修法：家族名 + 零件号**分开传**（与原厂 Android.mk 一致）

原厂 `wlan/Android.mk:14` 就是这么干的：

```make
WIFI_OPTS := CONFIG_MTK_COMBO_WIFI_HIF=$(WIFI_HIF) MODULE_NAME=$(WIFI_NAME) \
             MTK_COMBO_CHIP=$(WIFI_CHIP) WLAN_CHIP_ID=$(WLAN_CHIP_ID) ...
```

于是改成：

```bash
build_mod wlan  MTK_COMBO_CHIP=CONNAC WLAN_CHIP_ID=MT6761 ...
```

- `MTK_COMBO_CHIP=CONNAC` → 家族命中 `filter CONNAC` → `-DCONNAC` + 编进 `connac.o`。
- `WLAN_CHIP_ID=MT6761` 显式给出零件号 —— **必须显式**，因为 `Makefile:71` 的
  兜底是 `WLAN_CHIP_ID=$(word 1, $(MTK_COMBO_CHIP))`，现在会得到 `connac`，
  模块名会变成 `wlan_connac_axi.ko`。显式传则维持 `wlan_mt6761_axi.ko`。

#### 家族名是 `CONNAC` 的依据（全部来自**原厂 32 位模块**，符号表反查）

| 证据 | 原厂 `wlan_drv_gen4m.ko` |
| --- | --- |
| `mt66xx_driver_data_connac` | **GLOBAL OBJECT 存在** |
| `mt66xx_driver_data_connac2x2` | 不存在 ⇒ `CONNAC2X2` 未定义 |
| `mt66xx_driver_data_soc3_0` | 不存在 ⇒ `SOC3_0` 未定义 |
| `mtk_wcn_wmt_wlan_reg` | **被引用** ⇒ 走 WMT 路径，`CFG_SUPPORT_CONNINFRA=0` |
| `mtk_wcn_wlan_reg` / `conninfra*` | 都不存在 ⇒ 确认不是 conninfra/SOC3_0 |
| `srcversion` | `533BB7E5866E52F63B9ACCB`，与我们编出的**完全一致** |
| `depends` | `wmt_drv,wmt_chrdev_wifi`（我们为 `depends=` 空 —— 见下） |

#### 新增门禁（都带反向对照）

- 门禁 2：`$KOUT/.../wlan/chips/connac/connac.o` 必须存在（紧跟 `build_mod wlan`）。
- 门禁 4：`wlan_drv_gen4m.ko` 的符号表里必须有 `GLOBAL OBJECT mt66xx_driver_data_*`，
  读的是 `strip -g` 之后的字节 —— 即 `mtk_axi_ids[0].driver_data` 非空。原厂模块 PASS、
  我们修复前的产物 FAIL（0 命中），两个极性都验过。

#### 一个已知的、无害的差异：`depends=` 为空

原厂模块 `.modinfo` 是 `depends=wmt_drv,wmt_chrdev_wifi`，我们的是空。
原因：我们把四个模块**分四次 make** 调用，modpost 看不到彼此的导出符号，所以跨模块依赖
统计不到。`depends` 只被 `modprobe` 用来决定加载顺序；我们是手动按序 `insmod`，
且符号解析走 `__ksymtab`/`__versions`，与 `depends` 无关 —— 实测 `wmt_drv` 已成功加载。

### 9.7 第三个根因：DTB 缺 `memory-region`，reserved-memory 不是 DMA pool ✅ 已修

**现象**：9.5/9.6 两个根因修完后，`wmt_drv` / `wmt_chrdev_wifi` / `wlan_drv_gen4m`
三个模块 `insmod` 全部 `RC=0`（不再 panic），但 **`wlan0` 不出现**，且 dmesg 里
`wlan_assistant` 写 NVRAM 被拒：`Wi-Fi driver is not ready for write NVRAM`。

**干净加载轨迹**（`dmesg -c` 后重载 wlan）：

```
zain_gl: initWlan enter
wlanCreateWirelessDevice:(INIT INFO) Create wireless device success
zain_axi: mtk_axi_probe enter pdev=ffffffc07cb80800
zain_axi: fallback node=ffffffc07ff03108
axiCsrIoremap:(INIT INFO) CSRBaseAddress:0xFFFFFF8016500000 ioremap region 0x100000 @ 0x18000000
zain_axi: CSR map ok
axiDmaSetup:(INIT ERROR) of_reserved_mem_device_init failed(-19)
zain_axi: DMA setup ret=-19
mtk_axi_probe:(INIT INFO) mtk_axi_probe() done, ret: -19
zain_axi: glRegisterBus ret=0 g_prPlatDev=ffffffc07cb80800
initWlan:(INIT INFO) initWlan::End
```

**为什么 `-19` 是致命的**（`os/linux/hif/axi/axi.c`，`mtk_axi_probe`）：

```c
ret = axiDmaSetup(pdev, prDriverData);
if (ret)
        goto exit;                     /* ← 直接跳走 */
...
mtk_wcn_wmt_wlan_reg(&rWmtCb);         /* WMT 回调注册被整个跳过 */
```

DMA 初始化一失败，**WMT wlan 回调从未注册** → wlan 永不上电 → 没有 `wlan0`，
且除了这一行 ERROR 之外**没有任何其他报错**（所以极易误判为"模块加载成功但网卡没出现"）。

#### 定位过程：先证伪"源码看错"，再锁定 DTB

`axiDmaSetup` 被 agui 的两个补丁改过（`vendor/archimedes-wlan/0001-*.patch` +
`Documentation/wifi-coherent-dma-58c7ba5.patch`）。把两个补丁**按序**打到干净源码上
（`patch -p1`，`git apply --3way` 因嵌套仓库索引不匹配会部分失败，**必须用 `patch`**），
得到真实代码：

```c
#if AXI_CFG_PREALLOC_MEMORY_BUFFER            /* = 1, os/linux/hif/axi/include/hif.h:98 */
    if (!gWifiRsvMemPhyBase || !gWifiRsvMemSize) { /* ① 解析 DT memory-region */
            rmem_np = of_parse_phandle(pdev->dev.of_node, "memory-region", 0);
            ...
    }
    if (!gWifiRsvMemPhyBase || !gWifiRsvMemSize) { /* ② 拿不到才 -ENOMEM(-12) */
            ret = -ENOMEM; goto exit;
    }
    ret = of_reserved_mem_device_init(&pdev->dev);  /* ③ 实测在这里 -19 */
    if (ret) { DBGLOG(..., "of_reserved_mem_device_init failed(%d)\n", ret); goto exit; }
#endif
```

日志里**没有** ①② 的 `wifi reserved memory unavailable`、返回的也是 **-19 而不是 -12**
⇒ 全局量 `gWifiRsvMemPhyBase`/`gWifiRsvMemSize` **开机就非 0**（① ② 被跳过），
失败点唯一：③ 的 `of_reserved_mem_device_init`。

#### 两个全局量的来源（内核提供，不是模块）

`drivers/misc/mediatek/connectivity/common/connectivity_build_in_adapter.c`：

```c
phys_addr_t gWifiRsvMemPhyBase;        EXPORT_SYMBOL(gWifiRsvMemPhyBase);
unsigned long long gWifiRsvMemSize;    EXPORT_SYMBOL(gWifiRsvMemSize);
int reserve_memory_wifi_fn(struct reserved_mem *rmem)
{   gWifiRsvMemPhyBase = rmem->base;  gWifiRsvMemSize = rmem->size;  return 0; }
RESERVEDMEM_OF_DECLARE(reserve_memory_wifi, "mediatek,wifi-reserve-memory",
                       reserve_memory_wifi_fn);
```

`RESERVEDMEM_OF_DECLARE` 是**按 compatible 扫 `reserved-memory` 子节点**触发的，
**不需要** `memory-region` 句柄 —— 所以原厂 32 位驱动（符号表里同样只有
`gWifiRsvMemPhyBase` UND、**没有** `of_reserved_mem_device_init`）能正常工作，
而这份较新的 Gen4M 驱动多调了一次 `of_reserved_mem_device_init`，就撞上了缺口。

#### 真因：DT 两处缺失

```
# 设备实际 DT（= port/wifi_stock.dtb，md5 3a04f8f0… 与 port/4g_oem_boot.dtb 相同）
wifi@18000000 { compatible="mediatek,wifi"; reg=...; interrupts=...; }   ← 无 memory-region

reserved-memory/wifi-reserve-memory {
    compatible = "mediatek,wifi-reserve-memory";   ← 只有 MTK 自己的 compatible
    no-map; size = <0x0 0x300000>; alignment = <0x0 0x1000000>;
    alloc-ranges = <0x0 0x40000000 0x0 0x80000000>;  ← no-map 动态分配，不是 DMA pool
};
```

`of_reserved_mem_device_init` 的两级失败：

| 级 | 检查 | 缺失时的返回 |
|---|---|---|
| 1 | `of_parse_phandle(dev->of_node, "memory-region", 0)` | 空 → **-ENODEV(-19)** ← 实测 |
| 2 | `rmem->ops` 必须是 `rmem_dma_ops`（`ops->device_init`）| 空 → -EINVAL(-22) |

第 2 级的坑在于：**`shared-dma-pool` 有两个 `RESERVEDMEM_OF_DECLARE`**，靠 `no-map` 分流：

| 声明处 | 函数 | 对 `no-map` 的态度 |
|---|---|---|
| `drivers/base/dma-contiguous.c`（Makefile 先链接）| `rmem_cma_setup` | **有 `no-map` → 返回 -EINVAL，跳过** |
| `drivers/base/dma-coherent.c`（后链接）| `rmem_dma_setup` | 安装 `rmem->ops = &rmem_dma_ops` **→ 胜出** |

⇒ **`no-map` 必须保留**（删了反而会被 CMA 抢走）。

**决定性证据**：`port/4g_stock.dtb`（更新版 4G 固件，同一 SoC）里同一个节点**已经修好**：

```dts
wifi-reserve-memory {
    phandle = <0xa8>;
    reg = <0x0 0x7f000000 0x0 0x300000>;
    compatible = "shared-dma-pool", "mediatek,wifi-reserve-memory";
    no-map; size = <0x0 0x300000>; alignment = <0x0 0x1000000>;
};
wifi@18000000 { compatible="mediatek,wifi"; memory-region = <0xa8>; ... };
```

两份 DTB 反编译后**只有 3 处不同**：这个节点、`memory-region`、和两个充电电流值
（`ac_charger_input_current` 1.1A vs 1.05A、`charging_host_charger_current` 0.5A vs 1.5A）
—— 充电参数是**板级硬件差异，不能照搬 4G 的**，所以不能直接换 DTB。

#### 修法：`port/patch_wifi_dtb.py`（最小 FDT 手术，幂等）

自写 FDT 解析/重排器（不依赖 dtc），只改两个节点，其余字节（含充电参数）原样保留：

- `reserved-memory/wifi-reserve-memory`：加 `shared-dma-pool`、加 `reg = <0 0x7f000000 0 0x300000>`、
  删 `alloc-ranges`、**保留 `no-map`/`size`/`alignment`**、分配一个空闲 phandle（自动选中 **0xa8**，
  与 4G 那份一致）。
- `wifi@18000000`：加 `memory-region = <0xa8>`。

**`0x7f000000` 在本机不冲突**（从真机 `/proc/device-tree/reserved-memory/*/reg` 读的 bootloader 保留区）：

| 节点 | 范围 |
|---|---|
| `mblock-9-SPM-reserved` | `0x77ff0000` .. `0x78000000` |
| **本次占用** | **`0x7f000000` .. `0x7f300000`** |
| `mblock-7-framebuffer` | `0x7f980000` .. `0x7feb0000` |
| `mblock-6-SSPM-reserved` | `0x7feb0000` .. `0x7ffb0000` |
| `mblock-3-log_store` | `0x7ffbf000` .. `0x7ffff000` |

校验方式：修补后 `dtb_dump.py` 反编译，与 `4g_stock.dtb` 的 dump 做 diff ——
**只剩两个充电电流值**，即结构上与厂商修好的那份完全等价，而板级参数仍是 WiFi 版的。
`aw87329_pa`（`make_wifi_template.py` 的自检锚点）保留 4 处。

#### 坑：DTB 尺寸 vs 32 MiB 模板（第一次跑 CI 就炸了）

`make_wifi_template.py` 把内核区改成 `gzip + 新DTB`，再把原内核区之后的内容（ramdisk+padding）
原样接在后面。**模板正好塞满 32 MiB**，所以新 DTB 一旦比模板里的旧 DTB 大，字节数就溢出。
而 Python 的 `bytearray` **切片赋值越界不会报错，而是自动扩容**：

```
output=wifi-template-32MiB.img size=33554542      ← 应为 33554432
wifi_dtb_size=84204
ValueError: template must be an exact 32 MiB Android boot image   ← 报错发生在下一步，误导
```

第一版修补器输出 **84204 字节**，比模板里的 84094 大 110。两个原因，都很隐蔽：

| | size_struct | size_str | 合计 |
|---|---|---|---|
| `wifi_stock.dtb`（原始）| 74400 | 9576 | 84032 |
| `4g_stock.dtb`（厂商修好版）| 74448 | 9590 | **84094** |
| 第一版输出 ❌ | 74448 | **9699** | **84204** |
| 修正后 ✅ | 74448 | 9590 | **84094** |

1. **字符串块被整体重建**：正确做法是**保留原字符串块、只把新名字追加在末尾**（原有属性的
   `nameoff` 全部不变）。重建会凭空多出 123 字节。
2. **别给 DTB 末尾补对齐**：厂商那份 `totalsize=84094`（`%4==2`），本来就不对齐；补 2 字节反而溢出。

注意 `shared-dma-pool` 是属性**值**（在 struct 块里、属于 `compatible` 的内容），**不占字符串表**；
字符串表只需要新增 `memory-region`（14 字节）—— 这也正是"修好后尺寸与厂商版完全相同"的原因。

修正后：`make_wifi_template.py` 输出**正好 33554432**；并加了显式护栏（`len(new_region) > ks`
直接抛带具体字节数的错）+ 32 MiB 断言，避免以后再出现"报错点与病根不在同一处"。
`verify_build_script.py` 第 10 组也加了尺寸相等断言（`patched=84094 ref=84094`，含反向对照）。

#### CI 接线

`build.yml` 在 "Package boot images" 之前新增一步
`Patch the Wi-Fi DTB (reserved-memory DMA pool for the Gen4M driver)`，
`--wifi-dtb` 由 `port/wifi_stock.dtb` 改为 `port/wifi_archytas.dtb`（stage2 与 both 两处）。
`port/build_archytas_wifi.sh` 同步（本地/手动构建走同一条路）。

### 9.8 「多组 CI 打包 boot 报错」= 尺寸坑，已修并验证 ✅（2026-10-04 晚）

**4 个 run 完全同症状**，且**全部挂在同一步** `Package boot images`：

| run | commit | 失败步骤 |
|---|---|---|
| 37195442946 | c7e51b5c | Package boot images |
| 37195448569 | c7e51b5c | Package boot images |
| 37195532153 | 41dd1110 | Package boot images |
| 37195577635 | 757fd7ca | Package boot images |

`84fcadd1` 及更早全绿 ⇒ 是 `c7e51b5c`（引入 DTB 补丁那笔）带进来的。
新增的 "Patch the Wi-Fi DTB" 步骤本身**每一步都成功**，只是把 DTB 做大了：

```
output=wifi-template-32MiB.img size=33554542      ← 比 32 MiB 多 110
wifi_dtb_size=84204
ValueError: template must be an exact 32 MiB Android boot image
```

`84204 - 84094 = 110`，与镜像超出量 **1:1 对应**。修法见上一小节（复用原字符串块 + 不补尾部 pad）。
`ebdf48b56` 推送后 run **37196396706 全绿**，`Package boot images` 通过。

#### 关键事实：真实模板余量 = 0，本地有一份**过期模板**

| 文件 | 大小 | kernel_region | 尾部 DTB | 说明 |
|---|---|---|---|---|
| `archimedes-kernel-64-main/tools/archimedes-boot-template-32MiB.img` | 33554432 | **10483221** | **84094** | **CI 真正用的**（workflow 把 `agui4541/archimedes-kernel-64` checkout 到 `kernel/`）|
| `port/wifi-template-32MiB.img` | 33554432 | 10483159 | 84032 | **本地过期残留**，已被 `*.img` 忽略；**别拿它复现** |
| `port/wifi_stock.dtb`（=`4g_oem_boot.dtb`）| 84032 | — | — | 补丁输入 |
| `port/4g_stock.dtb` | 84094 | — | — | 厂商同 SoC 修复版，作参照 |

真实模板 `kernel_region = gzip + 84094`，**零余量** ⇒ 补丁后 DTB 必须**正好** 84094 才放得下。
本地端到端实测：补丁 84094 → 真模板重打包 → **输出精确 33554432，kernel_region 仍为 10483221**（尺寸中性）。

`verify_build_script.py` 新增**第 11 组**（共 **87 项 PASS**）：拿**真实模板**按 workflow 的方式实跑
`make_wifi_template.py`，断言输出精确 32 MiB、补丁 DTB 完整、kernel_region 不变；并用一个
"合法但大 1 字节"的 FDT 做**负向对照**，确认护栏拒绝且不落盘。第 1–10 组只验证零件、从不实跑打包，
这正是本次本地看不出来、却在 CI 炸掉的盲区。

### 9.9 第三个根因已真机确证；Wi-Fi 仍不通，卡在**第四层（WMT/STP）**❌（2026-10-04 晚）

刷入 `37196396706` 的 `boot-archytas-wifi.img`（sha256 `619cb20d…`，回读一致）重启后，
内核实际看到的 DTB 已是修补版：`compatible=shared-dma-pool\0mediatek,wifi-reserve-memory`、
`phandle=0xa8`、`no-map`、`reg=0x7f000000+0x300000`，`wifi@18000000` 有 `memory-region`。

**根因 3 的修复在真机上得到了教科书式的对比**：

| | 修复前 | 修复后 |
|---|---|---|
| `axiDmaSetup` | `of_reserved_mem_device_init failed(-19)` | `assigned reserved memory node wifi-reserve-memory`<br>`base=0x7f000000 size=0x300000, coherent DMA pool attached` |
| `mtk_wcn_wmt_wlan_reg` | **从未执行**（probe 提前 `goto exit`）| `wmt wlan cb register` ✅ |
| `mtk_axi_probe` | 中断 | `done, ret: 0` ✅ |
| 更深一层 | 到不了 | `halSetDriverOwn: DRIVER OWN Done` ← 芯片已上电<br>`wlanSetChipEcoInfo: Chip ID[0001] Version[E1] HW[0x8a00]` ← CONNAC 识别成功<br>`kalFirmwareOpen: WIFI_RAM_CODE_soc1_0_1_1.bin done` ← 固件已载入 |

**但 `wlan0` 仍不出现。** `wifi@1.0-service` 反复重试，WMT 每次都拒绝：

```
wmt_func_wifi_on: wmt wlan func on before wlan probe
update_driver_loaded_status: 0
wmt_func_wifi_on(690): wmt call wlan probe fail(-1)
WIFI_write[E]: WMT turn on WIFI fail!
```

#### 第四层：固件下载后的握手超时 → 触发整芯片复位（源码级定位）

完整栈回溯（`dump_stack` 来自 `glResetTrigger`）：

```
wmt_func_wifi_on → opfunc_wlan_probe → hifAxiProbe → wlanProbe+0x3b0
  → wlanAdapterStart+0x244            (fw_dl.c:1232   wlanDownloadFW)
     → wlanDownloadFW+0xe0            (fw_dl.c:2303   downloadFirmware)
        → wlanConnacFormatDownload+0x11c   (fw_dl.c:2232   wlanConfigWifiFunc)
           → wlanConfigWifiFunc+0x248      (fw_dl.c:1624   wlanConfigWifiFuncStatus 等响应)
              → glResetTrigger+0x30        (fw_dl.c:1628   GL_RESET_TRIGGER)
```

```c
/* wlan/core/gen4m/chips/common/fw_dl.c:1622 */
DBGLOG(INIT, INFO, "FW_START CMD send, waiting for RSP\n");
u4Status = wlanConfigWifiFuncStatus(prAdapter, ucCmdSeqNum);
if (u4Status != WLAN_STATUS_SUCCESS) {
    DBGLOG(INIT, INFO, "FW_START EVT failed\n");
    GL_RESET_TRIGGER(prAdapter, RST_FLAG_CHIP_RESET);   /* ← 喷栈点 */
}
```

`wlanProbe: probe failed, reason:3` 里的 **3 = MTK 的 `WLAN_STATUS_FAILURE`**（不是枚举序号）；
`wlanAdapterStart: Fail reason: 5` 对应 `RAM_CODE_DOWNLOAD_FAIL`。
`wlanAdapterStart` 的失败枚举（`common/wlan_lib.c:1121`，0 基）：
`0=ALLOC_ADAPTER_MEM_FAIL, 1=DRIVER_OWN_FAIL, 2=INIT_ADAPTER_FAIL, 3=INIT_HIFINFO_FAIL,
4=SET_CHIP_ECO_INFO_FAIL, 5=RAM_CODE_DOWNLOAD_FAIL, 6=WAIT_FIRMWARE_READY_FAIL`。

**但真正的第一因在更下层 —— 芯片对 STP 命令完全没有回应：**

```
[HIF-SDIO][E] wmt_lib_put_act_op(1500): opId(20) completion timeout
[HIF-SDIO][E] wmt_dev_rx_timeout(461): gRxCount != 0 (-1), reset it!   ← -1 是垃圾值
[WMT-CORE][E] opfunc_pwr_sv(1534): wmt_core: read HOST_AWAKE_EVT fail(-1) len(0, 6), host trigger f/w assert
              → wmt_lib_cmb_rst: [whole chip reset] ok!
...           → wlanConfigWifiFunc: FW_START CMD send, waiting for RSP
              → 320 ms 后又一次 whole chip reset（这次由 stp_btm 发起）
              → FW_START EVT failed → glResetTrigger
```

即：**固件已成功写入芯片 RAM，但芯片从此不再应答任何 STP 命令**（连 WMT 的 HOST_AWAKE 握手都超时）。
`wlanConfigWifiFunc` 等不到 `FW_START` 响应事件（320 ms 超时），于是 `GL_RESET_TRIGGER` 复位芯片，
`wlanProbe` 返回失败，`wmt_func_wifi_on` 得到 `fail(-1)`，`wifi@1.0-service` 无限重试 → 永无 `wlan0`。

**已排除**（都正常）：`/vendor/firmware` 固件齐全（`WIFI_RAM_CODE_soc1_0_1_1.bin`、
`soc1_0_patch_mcu_1_1_hdr.bin`、`soc1_0_ram_{mcu,bt,wifi}_1_1_hdr.bin`、`WMT_SOC.cfg` 12 个文件）；
`WMT_SOC.cfg` 内容正常；CONSYS HW 层 probe 正常（EMI 清空、EMI MPU、`conn2ap_sw_irq id(242)`）；
`halSetDriverOwn` / Chip ID 读取正常（AXI 窗口是活的）。

**下一步建议**（尚未验证）：
1. 与**能工作的 4G 板**做 A/B：同一内核、同一批 `.ko`，只换 DTB/ROM —— 差异只可能是
   板级 DTB（`consys`/`wmt` 属性、`conn2ap_sw_irq`）或 `/vendor`（firmware / `init.wlan_drv.rc` /
   `WMT_SOC.cfg`）。这是最省时的切入口。
2. 重点核对 STP 用的 **CONSYS EMI 共享内存**（`Clearing Connsys EMI physical(0xbf000000) 4194304 bytes`）
   与 **`conn2ap_sw_irq`**（DTS 里 id 242、trigger 1）：`gRxCount == -1` 这种垃圾值提示
   STP 的 EMI/中断结构与驱动预期不在同一处。
3. 注意 `vendor.connsys.driver.ready=yes` 会触发原厂 init 去 `insmod
   /vendor/lib/modules/{wmt_chrdev_wifi,wlan_drv_gen4m}.ko`，这两条**必然 `Exec format error`**
   （`/vendor` 内仍是 32 位原厂模块）。目前故意不覆盖以免开机循环，但它可能会打断该 rc 里
   后续的上电动作 —— 值得对照 `init.wlan_drv.rc` 逐条确认。

### 9.10 CI 触发过滤：不该编的推送就别编（2026-10-04）

本日 `e6e309186` 只改了自检脚本，却照样跑了 **11 分钟全量构建**（run 37196634842，success）。
`build.yml` 的 `push` 触发器现在带 `paths-ignore`：

```yaml
  push:
    branches: [ main ]
    paths-ignore:
      - '**/*.md'
      - 'port/verify_build_script.py'
      - 'port/ci_*.py'
      - 'port/dtb_*.py'
      - 'port/analyze_oem_diff.py'
      - 'port/check_ramdisk.py'
      - 'port/dump_relocs.py'
      - 'port/ext4_read.py'
```

**名单是"从反面"建出来的：只放构建链零引用的文件。** 在 `build.yml` +
`build_connectivity_modules.sh` 里 grep 每个候选，构建真正读的 `port/` 文件只有：

`patch_adrp_reloc.py`、`patch_wifi_dtb.py`、`make_wifi_template.py`、`elf_symbols.py`、
`elf_debug_share.py`、`build_connectivity_modules.sh`、`k61v1_64_archytas_defconfig`、
`wifi_stock.dtb`、`4g_stock.dtb`

⇒ 这些**永远不能**出现在上面那张表里。特别提醒：`elf_debug_share.py` 和 `elf_symbols.py`
看名字像分析工具，其实**是构建依赖**（`build_connectivity_modules.sh` 会调用），
差点被误加。`flash_archytas.sh` 虽然 CI 不跑，也**保留不忽略**（保守起见）。

`verify_build_script.py` **第 12 组**把这个契约双向锁住（共 **91 项 PASS**）：
过滤器存在且只挂在 `push`；**没有任何构建输入被匹配**（用 `fnmatch`，并模拟 GitHub
"`**/` 可匹配零级目录"的语义）；文档/自检/CI 辅助/分析工具**确实**被匹配。
之所以要双向断言：静默忽略一个构建输入会留下一张**看似绿的过期镜像**，这是唯一真正危险的失败模式。

`workflow_dispatch`（快车道）与 `pull_request` **故意不过滤** —— 快车道要能手动随时跑，
PR 应当始终被验证。

### 9.11 第四个根因（DTB consys-reserve-memory 动态分配 vs bootloader 固定窗口）✅ 已修（2026-10-04 深夜）

**现象回顾**：前三个根因修完后，`wmt_drv` / `wmt_chrdev_wifi` / `wlan_drv_gen4m` 三模块 `insmod rc=0`，AXI probe 成功返回 0，固件下载成功（`kalFirmwareOpen: WIFI_RAM_CODE_soc1_0_1_1.bin done`），**但 FW_START 握手超时 → 整芯片复位循环 → wlan0 永不出现**。

关键异常（9.9 节日志）：
```
Clearing Connsys EMI physical(0xbf000000) 4194304 bytes   ← WMT 日志里的固定 4MB 清零
[HIF-SDIO][E] wmt_lib_put_act_op: opId(20) completion timeout
[HIF-SDIO][E] wmt_dev_rx_timeout: gRxCount != 0 (-1)       ← STP 接收计数垃圾值
[WMT-CORE][E] opfunc_pwr_sv: read HOST_AWAKE_EVT fail(-1)  ← WMT 握手也失败
```

#### 根因：`consys-reserve-memory` 的 `alloc-ranges` 与 bootloader 硬编码窗口不匹配

**DT 现状**（`wifi_stock.dtb` + `4g_oem_boot.dtb`，两份完全相同）：
```
consys-reserve-memory {
    compatible = "mediatek,consys-reserve-memory";
    no-map;
    size = <0x0 0x400000>;
    alignment = <0x0 0x1000000>;
    alloc-ranges = <0x0 0x40000000 0x0 0x80000000>;   ← 动态分配
};
```

**没有** `reg` 属性 → reserved-memory 分配器在 `0x40000000..0xC0000000` 内动态选地址。

**WMT / bootloader 那边**：日志里明确打印 `0xbf000000` —— bootloader/LK 和 WMT 驱动**硬编码** CONSYS EMI 窗口在这个固定地址。它们不读 DT，直接把 CONSYS 芯片的 EMI window 基寄存器编程为 `0xbf000000`。

**冲突**：
```
内核 gConEmiPhyBase     = DT 动态分配（X，某个 0x40000000..0xC0000000 内的值）
WMT / 芯片 EMI window    = 0xbf000000（硬编码）
```

两边对**同一个 4MB 共享内存**的物理地址理解不一致 → STP（Share Transport Protocol）的接收/发送结构体在 host 侧和 chip 侧各在不同位置 → 芯片写入 STP 头部的数据 host 永远读不到 → `gRxCount = -1`（0xffffffff）垃圾值 → 所有握手（HOST_AWAKE、FW_START）超时 → 芯片复位循环。

**为什么 4G 原厂内核能工作**：原厂 32 位内核 + 原厂 WMT 驱动的代码路径里，`gConEmiPhyBase` 可能也来自动态分配，但原厂内核可能通过别的机制（atag、bootloader 传参、或驱动里的 fallback）同步了地址。而我们的 64 位 archimedes 内核的 `connectivity_build_in_adapter.c`（`RESERVEDMEM_OF_DECLARE`）**只读 DT**，没有 atag fallback。

**关键证据链**：
1. `gl_init.c:5335` 赋值 `gConEmiPhyBaseFinal = gConEmiPhyBase;`（内核导出的 DT 分配值）
2. 固件下载走 AXI 窗口（直接 MMIO 到芯片 RAM，不依赖共享内存）→ 成功
3. STP / FW_START 握手需要共享内存 → 两边地址不匹配 → 超时
4. DTB diff（§1）显示 4G vs Wi-Fi 在 consys/wmt 节点上零差异 → 问题不在 DTBO/overlay
5. 4G 原厂更新版 DTB（`4g_stock.dtb`）里 `consys-reserve-memory` **也没有** `reg` —— 厂商只修了 wifi-reserve-memory（§9.7），没动 consys，说明原厂内核有 fallback 而我们没有

#### 修法：`patch_wifi_dtb.py` 扩展（零尺寸增长！）

`alloc-ranges`（16 字节 struct value）→ `reg`（16 字节 struct value），struct 块大小不变。字符串表完全不变：`reg` 已存在于原始块里（wifi-reserve-memory 已引用），`alloc-ranges` 虽不再被引用但整块保留不重建。**总 DTB 尺寸仍为 84094，与 4g_stock.dtb 相同**。

```
consys-reserve-memory {
    reg = <0x0 0xbf000000 0x0 0x400000>;       ← 新增，与 bootloader/WMT 对齐
    compatible = "mediatek,consys-reserve-memory";
    no-map;
    size = <0x0 0x400000>;
    alignment = <0x0 0x1000000>;
    // alloc-ranges 删除
};
```

**`0xbf000000` 不与任何保留区冲突**：
```
mblock-9-SPM-reserved    0x77ff0000 .. 0x78000000
wifi-reserve-memory(已修) 0x7f000000 .. 0x7f300000
mblock-7-framebuffer     0x7f980000 .. 0x7feb0000
mblock-6-SSPM-reserved   0x7feb0000 .. 0x7ffb0000
mblock-3-log_store       0x7ffbf000 .. 0x7ffff000
CONSYS EMI window (本次)  0xbf000000 .. 0xbf400000    ← DRAM 内、无冲突
DRAM 上限                0xC0000000
```

端到端验证（`patch_wifi_dtb.py` → `make_wifi_template.py --wifi-dtb`）：输出精确 **33554432 字节（32 MiB）**，kernel_region 尺寸不变。

幂等性验证：对已补丁 blob 再跑一遍 → `changed=False`、sha256 完全相同。

#### CI 自动接线

`patch_wifi_dtb.py` 已被 `.github/workflows/build.yml` 的 "Patch the Wi-Fi DTB" 步骤调用（§9.8），`make_wifi_template.py` 在两个 stage 都消费该产物。**改动只在 `patch_wifi_dtb.py` 内部**，CI yaml 无需变动。

⚠️ **副作用**：改的是 DTB → 必须重新刷 boot 镜像（不像前两个模块根因只改 `.ko`）。

#### 真机确认命令（若需进一步诊断）

如果刷入补丁 boot 后仍有 STP 问题，在设备上跑：
```sh
# 1. 确认 DT 里 consys-reserve-memory 现在有固定 reg
for f in /proc/device-tree/reserved-memory/consys-reserve-memory/*; do \
  echo -n "$(basename $f)="; xxd -p $f | head -1; done

# 2. 内核实际看到的 CONSYS EMI 基地址
adb shell 'cat /proc/kallsyms | grep gConEmiPhyBase'
adb shell 'echo gConEmiPhyBase | busybox nc -p /proc/kallsyms'  # 或直接从 dmesg 看

# 3. /proc/interrupts 里 conn2ap_sw_irq (242) 是否有计数
adb shell 'grep -E "242|conn2ap" /proc/interrupts'

# 4. 系统 DRAM 布局（确认 0xbf000000 在 RAM 内）
adb shell 'cat /proc/iomem | head -20'
adb shell 'ls /sys/firmware/devicetree/base/reserved-memory/'
```

### 9.12 CONSYS EMI 窗口固定：真机验证结果（2026-10-05）

对应 9.11 的修复（提交 `37b50bbca Fix:WiFi STP Problem`，CI run **37200755460**，success）。
镜像 `boot-archytas-wifi.img` sha256 `3214837a…c376021`；刷入前备份原 boot
（`619cb20d…`，即 run 37196396706 的镜像，可随时回退），写 `/dev/block/mmcblk0p25`
后回读 sha256 一致。真机 `/proc/device-tree/reserved-memory/consys-reserve-memory/reg`
实测 `00 00 00 00 bf 00 00 00 00 00 00 00 00 40 00 00` → base **0xbf000000** / size
**0x400000**，`alloc-ranges` 已消失（目录只剩
`alignment/compatible/name/no-map/reg/size`）。DTB 仍 84094，32 MiB 预算不变。

#### 结论：修复有效 —— 但墙往后挪了一层，`wlan0` 仍未出现

**已消失的老 signature**（对照 9.11 记录）：

| 老 signature | 现状 |
| --- | --- |
| `HOST_AWAKE_EVT fail(-1)` / `opfunc_pwr_sv` 失败 | 不再出现，电源握手正常 |
| `wmt_lib_put_act_op` completion timeout | 0 次 |
| `gRxCount != 0 (-1)`（垃圾值） | 变成 **`1`** |
| 芯片完全不应答 | 有应答：`patch dwn:0 frag(27,516) ok`、`btif_tx:recovered,len(1000),retry_left(8)`、`SW Rst succeed`、`UTC_SYNC_CMD OK`、consys HW/FW id 均 `0x8a00` |

**仍在**：`wlanProbe` 真的跑起来了（以前从没跑到），但 320 ms 后
`FW_START EVT failed` → `glResetTrigger` → `wlanAdapterStart Fail reason:5`
→ `wlanProbe failed reason:3`。`wmt_core_dump_func_state` 里始终 `w:0 stp:0`
（WLAN function 从未打开）。4 个模块 insmod 全 `rc=0`，4 个 dev 节点齐全
（`wmtWifi` 153 / `wmtdetect` 154 / `stpwmt` 190 / `fw_log_wmt` 224）。

**新证据（最关键）**：固件自己报了异常 —— 芯片静音时不可能有这个：

```
stp_trace32_dump:[I] [len=116][type=5]<EXCEPTION>, Dump=25, id=0x2 WIFI,
  isr=0x0, irq=0x12{LP=0x57484}{ITYPE=0x1}{EVA=0xF0400000}{isr_t=0}{exp_t=39134}
```

即：固件已能下载、能跑，但在 FW_START 阶段 crash。
`EVA=0xF0400000` 在 `/proc/iomem` 里查不到（iomem 只有 `AXI-BUS 18000000-180fffff`），
`4g_stock.dts` 里也没有这个地址 → **下一步就查这个地址属于谁**。
与「能工作的 4G 板」A/B（同一内核、同一批 `.ko`，只换 DTB/ROM）仍是最省时的切入口。

顺带记录：`halShowDmaschInfo` 的 DMA scheduler dump 显示 group15
`rsv_cnt=0x0b0 / src_cnt=0xf70 / pktin_cnt=0x06` 且多处 `mismatch!`，
group0~14 则全为 0 —— 若后续要查，可从这里入手。

#### ⚠️ 验证时的时序陷阱（差点误判成"修复无效"）

原厂 init 的 `insmod` 在 t=90.4 因 32 位模块 `Exec format error` 失败，
而 `wmt_launcher` **t=90.7 就已给芯片上电**；手动 insmod 在 t=101.9 才注册 WLAN 回调
⇒ 上电早于驱动，`wlanProbe` 根本没机会跑，日志里连 `wlanProbe` 都不出现。

**正确顺序**：先 insmod 四个 `.ko`，确认
`/dev/{wmtWifi,wmtdetect,stpwmt,fw_log_wmt}` 齐全，
再 `stop wmt_launcher; start wmt_launcher` 重新上电。
`setprop vendor.connsys.driver.ready yes` **不能**用来重试 —— 它只会再次触发
init 那三条同样失败的 32 位 `insmod`，并把芯片 power off。

---

*生成/修订于 2026-10-02。工具链：自写 `dtb_dump.py`（反编译）、`dtb_diff.py`（全量 diff）、`make_wifi_template.py`（DTB 替换）、`analyze_oem_diff.py`（原厂对比）、`patch_wifi_dtb.py`（DTB 最小手术，2026-10-04 增）、`elf_symbols.py`（模块符号检查）、`peek_ko.py`（2026-10-05 临时调试用）。权威证据：4G/ Wi-Fi 双方原厂 `boot.bin`+`dtbo.bin` 抽出的 DTB 与 `diff_main_oem.txt`/`diff_dtbo_oem.txt`。*

### 9.13 第五个根因：`CFG_MTK_ANDROID_EMI=0` 让 EMI 固件段被静默跳过 ✅ 定位并已修（2026-10-05）

**根因一句话**：wlan 模块构建时 `CFG_MTK_ANDROID_EMI` 宏默认 `0`，让
`wlanDownloadEMISection()`（`fw_dl.c:443`）编译成 8 字节 stub，所有带
`DOWNLOAD_CONFIG_EMI` tailer 的固件段被静默跳过 —— 芯片跑起来后
EMI 内存还是脏的，FW_START 阶段在 `0xF0400000` crash。

#### 证据链

1. **stp_trace32_dump 里的 EVA 就是 EMI 窗口**：`0xF0400000` = `0xF0000000`（CONNAC
   MCU 自己的 EMI window 基址，`/proc/iomem` 里看不到因为这是芯片内部地址空间）
   + `0x400000`（正好是 consys-reserve-memory 的 4 MiB）。这不是随机地址，是固件
   要读写的 EMI region 边界。

2. **旧产物 .ko 里 `wlanDownloadEMISection` = 8 字节**（`MOV X0, 0; RET`）：

   ```
   wlanDownloadEMISection  GLOBAL  FUNC  sz=8   0x151380
   kalSetEmiMpuProtection GLOBAL  FUNC  sz=4   0x154b70   ← compat_stub.c no-op
   gConEmiPhyBaseFinal     OBJECT         sz=8   0x1a260    ← compat_stub.c 零初始化
   gConEmiPhyBase          NOTYPE         sz=0              ← 内核符号
   ```

3. **`fw_dl.c` 源码**里 `#if CFG_MTK_ANDROID_EMI` 整个 block 包着
   `request_mem_region + ioremap_nocache + kalMemCopy + release_mem_region`。
   宏关掉，整块被预处理器踢掉，只留一个空函数返回 `WLAN_STATUS_SUCCESS`。

4. **为什么 ADRP / consys 修完后才暴露**：STP 握手通了 → 固件下载走完
   → EMI section 没被下载 → 固件一启动就 crash。之前 STP 还没通时，
   这个 bug 被更早的握手超时挡住了。

5. **`halShowDmaschInfo` group15 mismatch!** 旁证 —— DMA scheduler
   也是 EMI 相关路径，没 download 自然没数据。

#### 修复（`port/build_connectivity_modules.sh` step 2c + Gate #5）

1. **`build_mod wlan` 加 `MTK_ANDROID_EMI=y`**（THE REAL FIX）：
   wlan/core Makefile **自己就有一个 ifeq** 控制 EMI 开关：
   ```makefile
   # chips/Makefile:257-261
   ifeq ($(MTK_ANDROID_EMI), y)
       ccflags-y += -DCFG_MTK_ANDROID_EMI=1
   else
       ccflags-y += -DCFG_MTK_ANDROID_EMI=0   # ← 我们掉进了 else 分支！
   endif
   ```
   之前只传了 `MTK_ANDROID_WMT=y`，没传这个。GCC `-D` 参数**最后一个赢**，
   所以我们 sed 注入的 `-DCFG_MTK_ANDROID_EMI=1` 永远打不过 Makefile 后面
   追加的 `-D0`（CI run 37200755460 就是这样挂的，Gate #5 完美触发）。
   现在让 Makefile 自己选对分支，sed 注入的 `-DCFG_MTK_ANDROID_EMI=1` 留作兜底。

2. **`emi_compat.h` / `compat_stub.c` 调整**：
   - `gConEmiPhyBaseFinal` / `gConEmiSizeFinal` 改成 `__weak`（`gl_init.c` 在
     `#if CFG_MTK_ANDROID_EMI=1` 下自己定义强符号，我们的是 fallback）
   - `kalSetEmiMpuProtection` / `kalSetDrvEmiMpuProtection` 继续由 compat_stub.c
     提供（上游 MTK HAL 缺失），仍是 no-op（bootloader/conninfra 已配好 MPU）
   - `emi_compat.h` 保持 force-include，给所有 TU 提供 extern 声明

3. **Gate #5**：strip 之后检查
   - `wlanDownloadEMISection size > 16`（不再是 8 字节 stub）
   - `kalSetEmiMpuProtection` 符号存在（EMI 路径被引用）

#### 预期修复后的旧 .ko 对比

| 符号 | 旧 sz=8 (stub) | 新 sz≥100 (real) |
| --- | --- | --- |
| `wlanDownloadEMISection` | MOV X0,0; RET | request_mem_region + ioremap + kalMemCopy + release_mem_region |
| `gConEmiPhyBaseFinal` | 来自 compat_stub (weak) | 来自 gl_init.c (strong) |
| `kalSetEmiMpuProtection` | 来自 compat_stub (no-op) | 不变（HAL 仍缺），但**会被真代码调用** |

#### 真机验证清单（新 .ko 刷入后）

```sh
# 1. 先确认 EMI 真的在下载
dmesg | grep -i "EmiPhyBase\|emi.*download\|Consys emi"

# 2. halShowDmaschInfo 应该不再只有 group15 有 mismatch
dmesg | grep -A 30 "halShowDmaschInfo"

# 3. 固件 FW_START 应该过
dmesg | grep -i "FW_START\|HOST_AWAKE\|wlanProbe.*done\|axi probe.*done"

# 4. 终极检查
ip link show wlan0
```

---

## 9.15 🎉 **里程碑达成：Wi-Fi 完全通了**（2026-10-05 16:33 真机验证）

**累计 6 个根因层，每一层修复才能暴露下一层。** 这是完整的成功链：

```
# dmesg -s 65535 | grep -iE 'EmiPhyBase|FW_START|main_thread|scan|ping|connect'

[   35.041780] wlanDownloadEMISection: EmiPhyBase:0xbf000000 offset:0x177000, ioremap 0x400000
[   35.053092] wlanDownloadEMISection: EmiPhyBase:0xbf000000 offset:0x1e5000, ioremap 0x400000
[   35.053341] FW_START CMD send, waiting for RSP
[   35.072262] FW_START EVT success!!              ← 🎉 第一次成功！之前全 timeout
[   35.099277] main_thread:1159 starts running...   ← 驱动主循环正常启动
[   50.288177] [IDLE] -> [SCAN]                     ← 扫描开始
[   50.979083] scnEventScanDone: ScanDone           ← 扫描完成
[   50.979301] Total:7/8 ChinaNet-LKLy; Xiaomi_1202; HUAWEI-7FALZG  ← 扫到 AP！

# ip link show wlan0
9: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP mode DORMANT
    link/ether 00:08:22:20:f5:fb brd ff:ff:ff:ff:ff:ff

# ifconfig wlan0
inet addr:192.168.1.41  Bcast:192.168.1.255  Mask:255.255.255.0
RX packets:55 errors:0 dropped:0 overruns:0 frame:0
TX packets:109 errors:0 dropped:0 overruns:0 carrier:0

# ping -c 2 192.168.1.1
64 bytes from 192.168.1.1: icmp_seq=1 ttl=128 time=37.3 ms   ← 0% 丢包
```

### 6 层根因回顾

| # | 根因 | 修复 | 影响的层 |
|---|---|---|---|
| 1 | ADRP relocation 越界 → 内核模块加载崩溃 | `patch_adrp_reloc.py` 改 `module.c` | 内核层 |
| 2 | `CONFIG_MTK_COMBO_CHIP` 引号错配 → `mtk_wmt_probe` NULL deref | `k61v1_64_archytas_defconfig` | 配置层 |
| 3 | MTK_COMBO_CHIP 家族名错配 → `mtk_axi_ids[]` NULL | `k61v1_64_archytas_defconfig` | 配置层 |
| 4 | `wifi-reserve-memory alloc-ranges` 动态选地址 vs bootloader 固定 `0x7f000000` | `patch_wifi_dtb.py` 加固定 `reg` | DTB 层 |
| 5 | `consys-reserve-memory alloc-ranges` 动态选地址 vs bootloader 固定 `0xbf000000` → STP 共享内存不匹配 | `patch_wifi_dtb.py` 加固定 `reg` | DTB 层 |
| 6 | `CFG_MTK_ANDROID_EMI=0` → EMI 固件段被静默跳过 → 固件 crash at `EVA=0xF0400000` | `build_connectivity_modules.sh` 加 `MTK_ANDROID_EMI=y` | 模块构建层 |

### 原厂 init.rc 自动加载（2026-10-05 16:28）

**第一次**：原厂 init.rc 自动加载 64 位模块成功。之前需要手动 `insmod` 四个 .ko，
现在用 `install_wifi_modules.sh` 把 EMI 修复后的 64 位 .ko 覆盖 `/vendor/lib/modules/`
下的 32 位原厂版本（remount /vendor rw + push + chmod 0644），之后每次重启自动工作：

```bash
# 设备上电时序（现在完美）：
# t=34.95s  mtk_wmtd_worker insmod 四个模块（由 init.rc）
# t=34.96s  wlanProbe 进入 → FW 下载 → EMI section 下载 → FW_START EVT success
# t=35.09s  main_thread:1159 starts running...
# t=36.94s  wpa_supplicant SETSUSPENDMODE 0 → skip（暂时没触发死锁）
# t=50.28s  扫描开始 → 扫到 7 个 AP → 关联成功 → 拿到 IP
```

**之前手动 insmod 的时序陷阱自动消失**：之前手动 insmod 要等到 t=101.9，而
wmt_launcher 在 t=90.7 就已上电，驱动还没加载好 → 上电早于驱动。原厂 init.rc 在
boot complete 时就加载了驱动，wmt_launcher 还没 start → 驱动先于上电。

```bash
# 验证自动加载（重启后）：
lsmod | grep -iE 'wmt|wlan|bt'
# wlan_mt6761_axi      2994176  0
# wmt_chrdev_wifi        32768  1 wlan_mt6761_axi
# bt_drv                 36864  1
# wmt_drv              1396736  4 wlan_mt6761_axi,wmt_chrdev_wifi,bt_drv
getprop vendor.connsys.driver.ready   # → yes
ip link show wlan0                     # → state UP
```

### 后续 TODO

- [ ] **suspend 死锁根因**（workaround 已加，根因未挖）：`SETSUSPENDMODE 1`
  → `priv_driver_set_suspend_mode` → main_thread → `wlanSetSuspendMode` → 发 FW suspend cmd
  → 等 FW reply → FW 不回复 → 30s timeout × 4 = main_thread 死亡。
  **EMI 引入后才出现**（之前 EMI 是 stub，FW 状态不一致不会在 suspend 路径上卡住）。
  嫌疑：`gl_kal.c:kalSetSuspendFlagToEMI` 用 `gConEmiPhyBase`（无 Final 后缀），
  而 `wlanDownloadEMISection` 用 `gConEmiPhyBaseFinal` —— 两个不同全局变量，
  `gConEmiPhyBase` 可能未初始化 → `wf_ioremap_write(0+offset, ...)` 空指针写入。
- [ ] **CI 加 post-build 自动刷 vendor**（可选，手动更安全）。
- [ ] **更新 §7 待真机验证清单**。

### 构建产物版本

- 最后一次构建：`commit 97c25f1cd`（suspend workaround patch + EMI 完整修复）
- CI 构建后新 `archytas-wifi-modules-only.zip`：
  - wlan_drv_gen4m.ko —— suspend patch 后稍变（sed 替换 1 行 → 加 return 0，
    函数总 bytecode 量略有增减，不影响 ELF size）
  - 其他 3 个模块不变

---

## 9.16 **suspend 死锁 workaround**（2026-10-05 深夜）

**新的死锁**：Wi-Fi 能通了（§9.15 里程碑），但 `wpa_supplicant` 启动时立刻发
`SETSUSPENDMODE 1`（enable power save），驱动走到 `wlanSetSuspendMode` → main_thread
发 FW suspend command → **FW 不回复** → main_thread 持锁睡 30s timeout × 4 =
完全死亡。之后所有 driver ioctl 卡死（网页打不开、Wi-Fi 设置卡死）。

**为什么 §9.15 那次没触发**：那次 wpa_supplicant 发的是 `SETSUSPENDMODE 0`（disable），
驱动返回 `"Already in suspend mode [0], SKIP!"` 直接跳过了。这次是 `SETSUSPENDMODE 1`（enable），
`fgIsInSuspendMode != fgEnable` → 真走 suspend 路径。

### 根因分析

`suspend` 路径是 **EMI 引入后才出现的**（之前 EMI 是 stub，FW 根本没正常启动）。

两个嫌疑点：

1. **`kalSetSuspendFlagToEMI` 用错了全局变量**：
   ```c
   // gl_kal.c —— 用的是 gConEmiPhyBase（无 Final 后缀）
   if (!gConEmiPhyBase) {
       // 只在 CFG_SUPPORT_CONNINFRA == 1 时通过 conninfra_get_phy_addr 赋值
       // 否则保持 0！
   }
   wf_ioremap_write((gConEmiPhyBase + u4Offset), suspendFlag);
   ```
   而 EMI 下载用的是 `gConEmiPhyBaseFinal`（有 Final）——两个不同变量。如果
   `CFG_SUPPORT_CONNINFRA` 不是 1，`gConEmiPhyBase` 永远是 0 →
   `wf_ioremap_write(0 + offset, ...)` 空指针写入。

2. **FW 状态机未就绪**：EMI section 下载后 FW 需要一段时间初始化，
   但 suspend 请求可能在 FW 还在初始化时就到了 → FW 不回复。

### Workaround（`build_connectivity_modules.sh` step 2c-2）

**直接 patch 掉 suspend 调用**：

```bash
# gl_wext_priv.c — priv_driver_set_suspend_mode 函数里
# 把这行：
wlanSetSuspendMode(prGlueInfo, fgEnable);
# 替换成：
/* ARCHYTAS PATCH: deadlock workaround - skip suspend path entirely */
return 0;
```

**精确锚点**：`wlanSetSuspendMode(prGlueInfo, fgEnable);` 这个精确字符串
**只在 `priv_driver_set_suspend_mode` 里出现一次**（系统 suspend callback
用的是 `TRUE`/`FALSE` 字面量，不受影响）。

**代价**：Wi-Fi 永不进入 FW-level power save（耗电增加，但设备插着充电器测）。

**后续根因修复**：需要确认 `CFG_SUPPORT_CONNINFRA` 的值，如果是 0，那
`kalSetSuspendFlagToEMI` 里 `gConEmiPhyBase` 一直是 0 → 空指针写导致异常，
可能影响了 FW suspend command 的 reply 路径。

### 刷机验证（CI 新出包后）

```bash
# 1. 下载 commit 97c25f1cd 的 archytas-wifi-modules-only.zip
# 2. push 覆盖 vendor 分区（和之前一样）
adb remount
for m in wmt_drv.ko wmt_chrdev_wifi.ko wlan_drv_gen4m.ko bt_drv.ko; do
  adb push $m /vendor/lib/modules/$m
  adb shell chmod 0644 /vendor/lib/modules/$m
done
# 3. 重启
adb reboot
# 4. 验证：
lsmod | grep wmt  # 4 个模块加载
getprop vendor.connsys.driver.ready  # → yes
dmesg | grep -c 'wait main_thread'   # → 0！
ip link show wlan0                   # → state UP
ping -c 2 192.168.1.1                # → 0% loss
```

## 9.17 2c-2 / 2c-3 suspend workaround 的生效审计：**我们刷的其实是"干净版"**（2026-10-06）

PR `audit-agui-vendor` 的结论是「agui 4G 用同一套驱动、**零 workaround** 也能正常 suspend」，因此
怀疑我们在 `build_connectivity_modules.sh` 里多打的 **2c-2**（`gl_wext_priv.c` 跳过
`wlanSetSuspendMode`，见 §9.16）和 **2c-3**（`wmt_dev.c` 删掉 POWERDOWN 分支的 `schedule_work`）
是多余的。本节回答一个更基本的问题：**这两个 workaround 到底在哪些产物里真的生效了？**

### 结论先行

| 产物 | `wmt_fb_notifier_callback` | POWERDOWN 分支首条调用 | `priv_driver_set_suspend_mode` 内 | 2c-2 | 2c-3 |
| --- | --- | --- | --- | --- | --- |
| **agui 4G `vendor.img`**（参考） | 316 B | `queue_work_on` | 有 `wlanSetSuspendMode` + `p2pSetSuspendMode` | 无 | 无 |
| CI run `37200755460`（`37b50bbca`） | 380 B | `queue_work_on` | 有 | 无 | 无 |
| `archytas-wifi-modules-only.zip`（zip#1，**我们刷过并验证 wlan0 UP 的那版**） | 380 B | `queue_work_on` | 有 | **无** | **无** |
| `archytas-wifi-modules-only(3).zip`（zip#3「pyfix」） | **344 B** | **`osal_warn_print`** | **两处调用都消失** | **有** ✅ | **有** ✅ |

**所以：上一轮刷进机器、并据此宣布"Wi-Fi 通了"的 zip#1，是一个没有 2c-2/2c-3 的干净版。**
它能通靠的是内核侧修复（DTB `consys-reserve-memory` 固定 §9.11/9.12 + `CFG_MTK_ANDROID_EMI=1`
§9.13），与这两个 workaround 无关。而 zip#1 刷完后出现的 `wait main_thread timeout × 12`
死锁，正是 2c-2 本该压住的那条路径 —— 两者自洽。

### 时间线自洽性

```
16:06  zip#1  f436f2e0   ← 刷入验证（无 2c-2/2c-3）
16:57  commit 97c25f1cd  fix(wifi): patch priv_driver_set_suspend_mode ... (2c-2)
17:25  commit 83e238143  fix(wmt): patch wmt_fb_notifier_callback ...       (2c-3)
18:55  zip#2  f436f2e0   ← 字节未变：此时 2c-2/2c-3 的 sed patch 未生效
19:00  commit 03c000ef2  fix(build): replace fragile sed w/ python ...
19:04  zip#3  1acc8df6   ← 变了：python patch 生效，两个 workaround 都打上
```

即 **`83e238143` 的初版用 `sed` 实现，未在产物里生效**（18:55 的 zip#2 与 16:06 的 zip#1 逐字节
相同），所以才有了 19:00 的 `03c000ef2`（改 python heredoc）和 19:04 才真正带 patch 的 zip#3。

### 判据：为什么不能用字符串 / 符号表 / relocation 计数

三条路都试过，全部无效，记下来避免重蹈：

1. **字符串**：anchor `@@@@@@@@@@wmt enter early POWERDOWN` 是 `WMT_WARN_FUNC` 的格式串，patch 只
   删它**下一行**，字符串永远在。
2. **符号 DEFINED/UNDEF**：`queue_work_on` 在三种状态下都是 UNDEF（模块引用内核导出），区分不了。
3. **relocation 计数**（一度以为的"决定性判据"）：`schedule_work()` 是 inline 包装，
   `queue_work_on` 每个调用点一条 CALL26 reloc —— **但 GCC 的 tail-merge 会把 UNBLANK 和
   POWERDOWN 两个完全相同的 `schedule_work(); break;` 折叠成同一个块**，所以**pristine 版也
   只有 1 条**。实测 agui（公认 pristine）和 zip#1（pristine）都是 1 条，且是同一条。

**唯一可靠的判据是 POWERDOWN 分支自己调用了什么。** switch 编译成
`cmp wN,#0; b.eq unblank` / `cmp wN,#4; b.ne default`，所以 **`cmp wN,#4` 紧跟 `b.ne` 就是
POWERDOWN 分支头**，其第一条 BL：

- pristine → `bl queue_work_on`（就是那个 `schedule_work`）
- patched → `bl osal_warn_print`，随后无条件跳回 epilogue（`mov w0,#0; ret`）

**自证**：agui 参考版（文档明确"零 workaround"）被本判据判为 PRISTINE，与预期一致。

zip#1 反汇编片段（POWERDOWN 分支，`cmp w19,#4` 于 `0x49194`）：

```
0x491b4/0x491b8/0x491bc:  atomic_set(1,0,0)      ← POWERDOWN 标志
0x491c0:  cbnz w1, 0x49284
0x491d8:  bl   queue_work_on                     ★ 仍然 schedule_work → 未 patch
```

zip#3 同一位置：

```
0x491b4/0x491b8/0x491bc:  atomic_set(1,0,0)
0x491c0:  cbz  w1, 0x49174
0x491d8:  bl   osal_warn_print                   ← 只剩 WMT_WARN_FUNC 日志
0x491dc:  b    0x49174                            ★ 直接返回 → 已 patch
```

### 新增工具

- **`port/extract_modules.py`** —— 从裸 ext4 `vendor.img` 提取 `.ko`。既有 `ext4_read.py` 的
  `cat` 只能出文本，未 strip 的 54 MB `.ko` 会按 UTF-8 解码失败。
- **`port/audit_wmt_fb_workaround.py`** —— 2c-3 判定（POWERDOWN 分支首条调用）。
- **`port/disasm_arm64.py`** —— 极简 AArch64 线性反汇编（定长 32 位，逐 4 字节步进即可），
  标注 B/BL/B.cond/CBZ/RET/ADRP 并解析 BL 的 reloc 符号名。两个脚本共用
  `audit_wmt_fb_workaround.ElfRel`。

写脚本时踩的坑（都已注释进代码）：`Elf64_Shdr` 字段序写错会静默读到垃圾；**ELF64 的
`r_info` 是 `(sym << 32) | type`，与 ELF32 相反**，写反则符号名全空、匹配数恒为 0；
`.shstrtab` 依赖**后缀共享**，用"段起点"字典查 `sh_name` 会漏掉 `.text`（它是 `.rela.text`
的子串）而让所有 section 失去名字。

### 待定

- agui 4G 两个 workaround 都没有也能正常工作，而 zip#1（同样没有）却出现 `main_thread`
  死锁 —— 说明差异不在"是否有 workaround"，而在别处（EMI 映射方式？芯片状态？）。若要真正
  对齐 agui，应先解释这个差异，再决定 2c-2/2c-3 的去留。
- zip#3（同时带 2c-2 + 2c-3）尚未真机验证。

## 9.18 agui 方案的验证：**workaround 与这个死锁无关**，真凶是 scan-done 里的无出口循环（2026-10-06）

### 9.18.1 设备上装的到底是哪版（哈希级确证）

用户把模块直接换进了 vendor 分区。核对结果：

| 位置 | 时间戳 | `wmt_drv.ko` | `wlan_drv_gen4m.ko` |
|---|---|---|---|
| `/vendor/lib/modules/`（设备） | 2026-10-05 19:04 | `1acc8df6…` | `e2ee1fe4…` |
| `_ci/latest_modules_pyfix/`（主机，zip#3） | — | `1acc8df6…` | `e2ee1fe4…` |

**逐字节相同 ⇒ 设备运行的就是 zip#3**（2c-2 + 2c-3 都已生效，见 §9.17）。
加载路径也确认了，原厂 init 就是读 vendor：

```
/vendor/etc/init/init.wmt_drv.rc : on boot                              → insmod wmt_drv.ko
/vendor/etc/init/init.wlan_drv.rc: on property:vendor.connsys.driver.ready=yes
                                   → insmod wmt_chrdev_wifi.ko
                                   → insmod wlan_drv_${ro.vendor.wlan.gen}.ko   # gen4m
                                   → start wlan_assistant
```

> 顺带印证了 **agui 的 vendor-lib64 方案本身是成立的**：arm64 `.ko` 放进
> `/vendor/lib/modules/` 后，开机 init 自动 insmod 成功，不再出现 32 位模块的
> `Exec format error`，`vendor.connsys.driver.ready=yes`、4 个模块与 4 个 dev 节点齐全。

### 9.18.2 正常路径：Wi-Fi 完全可用 ✅

干净开机（未做任何手工 insmod）：

```
wlanProbe → wlanAdapterStart → wlanDownloadEMISection: EmiPhyBase:0xbf000000 offset:0x177000 ioremap 0x400000
                             → EmiPhyBase:0xbf000000 offset:0x1e5000 ioremap 0x400000
          → wlanConfigWifiFunc: FW_START EVT success!!
          → wlanProbe: probe success, feature set: 0x84881418
          → wmt_func_wifi_on: wmt call wlan probe ok
```

`svc wifi enable` 后实测：

| 项 | 结果 |
|---|---|
| `wlan0` | `<BROADCAST,MULTICAST,UP,LOWER_UP> state UP` |
| 地址 | `inet 192.168.1.61/24`（另一次为 `192.168.1.62`），路由/`net.dns1=192.168.1.1` |
| ping 网关 | 3/3，0% loss |
| ping 公网 `223.5.5.5` | 3/3，0% loss |
| `main_thread` | state=`S`，`stime` 2s 内只 +1（空闲） |
| `wait main_thread timeout` | **0 次** |

### 9.18.3 但一个动作就把控制面打死，且 **100% 可复现** ⚠️

`svc wifi disable`（本地断开）之后立刻：`main_thread` 变 `state=R`，`stime` 以
**≈100 ticks/s（满核）**增长、`nvcsw=nivcsw=0`（从不睡眠），`wlan0` 消失、拿不到 IP，
所有 OID 卡死 30.72 s：

```
[ 177.121482] (3)[1244:wpa_supplicant][wlan]kalIoctlByBssIdx:(OID WARN) wait main_thread timeout, duration:31261ms
[ 177.121503] (3)[1244:wpa_supplicant][wlan]mtk_cfg80211_del_key:(RSN WARN) remove key error:c0000001
[ 207.841481] (0)[1474:kworker/u8:23][wlan]kalIoctlByBssIdx:(OID WARN) wait main_thread timeout, duration:30720ms
```

触发链（dmesg 原文，t=145.859）：

```
mtk_cfg80211_disconnect:(REQ INFO) ucBssIndex = 0
kalIndicateStatusAndComplete:(INIT INFO) [wifi] wlan0 netif_carrier_off
kalIndicateStatusAndComplete:(INIT INFO) [wifi]Indicate disconnection: Reason=0 Locally[1]
aisFsmRunEventAbort:(AIS STATE) [0] EVENT-ABORT: Current State NORMAL_TR, ucReasonOfDisconnect:8
        ↓ 随后 main_thread 进入 scan-done 处理 → 死循环，永不返回
```

### 9.18.4 空转位置：`sysrq` NMI 回栈直接钉死

`echo l > /proc/sysrq-trigger`（需 root）连续 4 次采样，PC 全部落在同一处：

```
[<ffffff800109b904>] scanGetCurrentEssChnlList+0x134/0x5b0 [wlan_mt6761_axi]
[<ffffff800109b918>] scanGetCurrentEssChnlList+0x148/0x5b0 [wlan_mt6761_axi]
[<ffffff800109b91c>] scanGetCurrentEssChnlList+0x14c/0x5b0 [wlan_mt6761_axi]
   ↑ 调用者
[<ffffff8001056134>] aisFsmRunEventScanDone+0x304/0x550     [wlan_mt6761_axi]
[<ffffff800106bf4c>] mboxRcvAllMsg+0xfc/0x2c0               [wlan_mt6761_axi]
[<ffffff8000f76194>] wlanProcessMboxMessage+0x14/0x70       [wlan_mt6761_axi]
[<ffffff8000fd4250>] kalProcessTxReq+0x40/0x2a0             [wlan_mt6761_axi]
[<ffffff8000fde680>] main_thread+0x820/0xef0                [wlan_mt6761_axi]
[<ffffff80080ce6c0>] kthread+0xfc/0x100
```

反汇编 `scanGetCurrentEssChnlList`（文件偏移 0x12c7d0 起），**+0x130…+0x158 是一个没有出口的循环**：

```
0x12c900: a9410402  LDP   x2, x1, [x0, #16]     ; 取 next / prev
0x12c904: b4000042  CBZ   x2, 0x12c90c
0x12c908: f9000441  STR   x1, [x2, #8]         ; unlink
0x12c90c: b4000041  CBZ   x1, 0x12c914
0x12c910: f9000022  STR   x2, [x1]             ; unlink
0x12c914: b94012e1  LDR   w1, [x23, #0x10]     ; 计数器
0x12c918: a9017c1f  STP   xzr, xzr, [x0, #16]  ; 清空节点
0x12c91c: 51000421  SUB   w1, w1, #1
0x12c920: b90012e1  STR   w1, [x23, #0x10]
0x12c924: 17fffff7  B     0x12c900             ; ← 无条件回跳；x0 永不前进
```

`0x12c924` 编码 `000101` = **无条件 `B`**（imm26 符号扩展 = −0x24），循环体内**没有任何出口分支**
（两个 `CBZ` 只跳过 2 条指令）。而且 `x0` 在循环里从不被写 —— 每次都摘同一个节点、
计数器一直往下减。**进得去，出不来**。

### 9.18.5 决定性对照：workaround 碰不到这条路径

对同一函数取函数体字节哈希：

| 构建 | `scanGetCurrentEssChnlList` | `aisFsmRunEventScanDone` |
|---|---|---|
| **agui 4G（参考）** | 1364 B `26334e27…` | 1196 B `e1afe827…` |
| zip#1（**无** workaround） | 1388 B `7fd77484…` | 1268 B `87248304…` |
| CI `37200755460`（**无** workaround） | 1388 B `7fd77484…` | 1268 B `87248304…` |
| zip#3（**有** workaround，设备上装的） | 1388 B `7fd77484…` | 1268 B `87248304…` |

**有无 workaround，这两个函数字节完全一致** ⇒ 2c-2/2c-3 无论打与不打，
这条死循环都在。动态侧也吻合：zip#1（§9.16 时段）与 zip#3（本节）**都卡死**。

**附加线索**：agui 参考版同一函数里**不存在这种微型无出口循环** ——
它全部 11 条无条件回边的最小循环体是 **25 条指令 / 100 B**；我们最小的那条就是
**9 条指令 / 36 B**（即 0x12c900 那个）。这是与 agui 的**真实代码差异**，值得下一步 A/B。

### 9.18.6 逐条核对 agui 审计文档 §6 的三步

| agui §6 | 期望 | 实测 | 判定 |
|---|---|---|---|
| ① 跑四项不变量，确认与 agui 一致 | 四项 PASS | `gConEmiPhyBase` = **UNDEF**（吃内核 consys `0xbf000000` 导出）✅；`wlanDownloadEMISection` = 556 B 真实现且开机真跑（日志可见）✅；`priv_driver_set_suspend_mode` = 552 B（**2c-2 已替换**）❌；`wmt_fb_notifier_callback` = 344 B（**2c-3 已删 work**）❌ | 2/4 一致，2 项偏离 |
| ② 摘掉 2c-2/2c-3 恢复真 suspend | 死锁消失 | **前提不成立**：死锁点在 scan-done 路径，与 suspend/POWERDOWN 无关；有无 workaround 该路径字节相同，两种版本都卡死 | ❌ 证伪 |
| ③ 若仍卡死，回协议层抓 `main_thread` 时序 | — | **正确**：真凶就是驱动自己的 scan-done 处理死循环 | ✅ |

结论：**"卡死 = 我们多打两个 workaround" 这个假设，对本失败模式不成立。**
workaround 的去留可各自独立评估（它们是 suspend 路径的事），但不能指望摘掉它治好这个死锁。

### 9.18.7 复现与恢复

```bash
# 复现（干净开机、Wi-Fi 已连上并拿到 IP 之后）
adb shell svc wifi disable            # 本地断开
sleep 12
adb shell svc wifi enable
# 现象：main_thread state=R、stime 满核增长、wlan0 消失、kalIoctlByBssIdx 30.72s 超时
adb shell 'echo l > /proc/sysrq-trigger'   # NMI 回栈，PC 落在 scanGetCurrentEssChnlList+0x134

# 恢复：只能重启（驱动控制面不会自愈）
adb reboot
```

**下一手建议**：拿 agui 4G 的 `scanGetCurrentEssChnlList`（未 strip，符号齐全）与我们逐指令 diff，
确认 agui 版本是否有正确出口；再决定是源码侧重编、还是把 agui 的扫描路径逻辑移植过来。
`wlan-core @ ba2c5a5` 就是我们用的上游，先比 agui 用的那一版。

> **已执行 → 见 §9.19**：diff 做完了，agui 版有正确出口（条件回边 + 重载），我们全部 artifact 都没有；
> 根因是缺 `-mpc-relative-literal-loads`，已修并加了门禁。

---

## 9.19 真凶定案并修复：**缺 `-mpc-relative-literal-loads`** 让 GCC 11.4.0 把 drain 循环编成死循环（2026-10-06）✅ 已修

§9.18 把空转 PC 钉死在 `scanGetCurrentEssChnlList` 里，但只证明"workaround 与它无关"。
本节把它**做完**：拿到源码、逐指令对比 agui 参考版、定出**编译配置**根因，并落成三处改动。

### 9.19.1 逐指令 diff：agui 有出口，我们**没有**

用 `port/audit_scan_loop.py`（新，Capstone 判据的轻量版）扫我们**全部 10 份历史 artifact**：

| artifact | 回边 | 循环体内 64-bit LDR 重载 | 判定 |
| --- | --- | --- | --- |
| `37190391324` / `37193565582`（build #早期） | `B` 无条件 → 0x12c7d0 | **无** | BROKEN |
| `37194238796` / `37196396706` / `37200755460` | `B` 无条件 → 0x12c7e0 | **无** | BROKEN |
| `modules-only-20261005_160637`（zip#1）/ `latest_modules` | `B` 无条件 → 0x12c910 | **无** | BROKEN |
| `latest_modules_suspend` / `_powerdown` / `_pyfix`（zip#3，**设备上装的**） | `B` 无条件 → 0x12c900 | **无** | BROKEN |
| **`agui-4g-ko`（参考版）** | **`B.cond` → 0x111c00** | **`f9400321` = `ldr x1,[head]`** | **GOOD** |

即：**这不是某一次构建的偶发，而是我们工具链下 100% 确定性的误编**；agui 版是好的。

### 9.19.2 源码：循环本身完全正确

`wlan-core @ ba2c5a5`（就是脚本钉的那一版，已 clone 核对 `HEAD=ba2c5a57f…`），
`mgmt/ap_selection.c:1091` `scanGetCurrentEssChnlList()`：

```c
	while (!LINK_IS_EMPTY(prCurEssLink)) {
		prBssDesc = LINK_PEEK_HEAD(prCurEssLink,
			struct BSS_DESC, rLinkEntryEss[ucBssIndex]);
		LINK_REMOVE_KNOWN_ENTRY(prCurEssLink,
			&prBssDesc->rLinkEntryEss[ucBssIndex]);
	}
```

`include/link.h` 里三条宏展开后：

```c
	struct LINK { struct LINK_ENTRY *prNext, *prPrev; uint32_t u4NumElem; };  /* 24 B */
	/* LINK_IS_EMPTY   */ head->prNext == (struct LINK_ENTRY *)head
	/* LINK_PEEK_HEAD */ head->prNext == head ? NULL : ENTRY_OF(head->prNext, …)
	/* LINK_REMOVE_KNOWN_ENTRY */ { linkDel(entry); head->u4NumElem--; }
```

关键点：`linkDel()`（内联）在**删链表首元素时会写 `head->prNext`**，而本循环每次都删首元素 ——
也就是 `LINK_PEEK_HEAD` 的 `head->prNext` **绝不是循环不变量**。

### 9.19.3 反汇编（Capstone，权威读法）

我们（`latest_modules_pyfix`，`0x12c8d4` 起）：

```
0x12c8dc  ldr  x1, [x0, #0x178]   ; x1 = head->prNext —— 在循环外只读一次
0x12c8e4  cmp  x1, x23            ; LINK_IS_EMPTY
0x12c8e8  b.eq 0x12c928
0x12c8f4  add  x0, x0, #0x10
0x12c8f8  sub  x0, x1, x0
0x12c8fc  add  x0, x0, x22        ; x0 = head->prNext - 16，算一次就再也不算
0x12c900  ldp  x2, x1, [x0, #0x10]; entry->prNext / entry->prPrev
0x12c904  cbz  x2, …
0x12c908  str  x1, [x2, #8]
0x12c90c  cbz  x1, …
0x12c910  str  x2, [x1]
0x12c914  ldr  w1, [x23, #0x10]   ; ★ w1 = u4NumElem，把走链指针 x1 覆盖掉
0x12c918  stp  xzr, xzr, [x0, #0x10]
0x12c91c  sub  w1, w1, #1
0x12c920  str  w1, [x23, #0x10]
0x12c924  b    0x12c900           ; ★ 无条件回边，没有重载、没有出口
```

`stp xzr,xzr,[x0,#0x10]` 把 `entry->prNext/prPrev` 清零后，第二趟 `ldp` 读出 `x2=x1=0`，
两个 `cbz` 全部跳过，只剩"把 `u4NumElem` 每次减 1" —— **x0 永不前进，循环永不结束**。
与 NMI 四次回栈全部命中 `0x12c900`、`main_thread` 满核空转完全吻合。

agui（`0x111bdc` 起）：

```
0x111be0  cmp  x1, x25
0x111be4  b.eq 0x111c3c
0x111be8  mov  w3, w27
0x111bec  add  x4, x3, #1
0x111bf0  lsl  x3, x3, #4
0x111bf4  lsl  x4, x4, #4
0x111c00  sub  x0, x1, x4          ; ← 循环头：每趟重算
0x111c04  cbz  x1, 0x111fd0        ; ← （panic 保护）
0x111c08  add  x0, x0, x3
0x111c0c  ldp  x2, x1, [x0, #0x10]
   … 照旧 unlink …
0x111c20  ldr  x1, [x25]           ; ★ 重载 head->prNext
0x111c24  stp  xzr, xzr, [x0, #0x10]
0x111c28  ldr  w0, [x25, #0x10]
0x111c2c  sub  w0, w0, #1
0x111c30  str  w0, [x25, #0x10]
0x111c34  subs xzr, x1, x25
0x111c38  b.ne 0x111c00            ; ★ 条件回边
```

### 9.19.4 根因：**编译配置**，不是算法

| | agui 参考版 | 我们（全部 artifact） |
| --- | --- | --- |
| 编译器 | `GNU C89 14.2.0`（ubuntu-24.04） | **`GNU C89 11.4.0`**（CI `runs-on: ubuntu-22.04`） |
| `DW_AT_producer` 里的 codegen 开关 | `-mpc-relative-literal-loads -mcmodel=large … -fno-pic` | **`-mcmodel=large -fno-pic -fstack-protector`（缺前者）** |
| `R_AARCH64_ADR_PREL_PG_HI21(275)` 计数 | **0** | **28021** |

证据来源：CI 日志里 `report_relocs()` 自己打印的一行（run `37200755460`）

```
wlan_drv_gen4m.ko      ADRP(275/276) = 28021  codegen: -mcmodel=large -fno-pic -fstack-protector
```

内核**本来就打算两个都给**：`arch/arm64/Makefile:81` 用 `KBUILD_CFLAGS_MODULE` 给 `-mcmodel=large`，
紧邻的 `:62` 由 `$(call cc-option, -mpc-relative-literal-loads)` 给第二个。
`-mpc-relative-literal-loads` 的作用正是让 GCC 走 **PC-relative 字面量池**（`ldr xN, .Lpool`），
而不是 ADRP + GOT 槽间接寻址。少了它，我们等于跑在**内核从未测试过的组合**下，代价有两笔：

1. **28021 个 ADRP 重定位** → 才需要 §8.2/§8.3 里那套"改内核 loader"的手术（`patch_adrp_reloc.py`）；
   agui 是 0，根本不需要。
2. **至少一个循环被误编成死循环**（本节）。

### 9.19.5 修复：三处（`port/build_connectivity_modules.sh`）

1. **step 2d-2（根因对齐）**：把 `-mpc-relative-literal-loads` 显式注入四个模块 Makefile 的
   `ccflags-y` / `subdir-ccflags-y`，并加进 wlan 模块的 `KCFLAGS`，与 agui 的配置完全一致。
   幂等标记 `ARCHYTAS_PC_REL_LITERAL`。
2. **step 2c-4（源码侧保险）**：在 `mgmt/ap_selection.c` 的 drain 循环体末尾插一行
   `__asm__ __volatile__("" ::: "memory");`（内存屏障），幂等标记 `ARCHYTAS_SCAN_DRAIN_BARRIER`。
   内存 clobber 是硬屏障，任何编译器都**不允许**把内存读跨越它 —— 于是 `head->prNext` 每趟必重读，
   循环必然终止。**这条不依赖代码模型**：即使标志将来回退，这个循环也不会再变成死循环。
3. **Gate #6（防回归）**：构建收尾跑 `port/audit_scan_loop.py` 读**已 strip 的出货字节**，
   要求"条件回边 + 循环体内 64-bit LDR 重载"同时成立，否则 `exit 1`。
   这类缺陷**不会**在符号检查/加载/联网阶段暴露（模块能加载、能连、能跑流量，只在 drain 时卡死），
   只有读指令才能发现，所以必须做成硬门禁。

### 9.19.6 副作用与收益

- ADRP 重定位应从 28021 掉到 **0**（其余三个模块同理：`wmt_drv` 9217、`wmt_chrdev_wifi` 197、`bt_drv` 161）。
  产出物将**不再依赖**内核侧的 ADRP 补丁；§8.3 的 `patch_adrp_reloc.py` **保留**作为安全网（无 ADRP 时是空操作）。
- 构建日志里 `report_relocs()` 的 `codegen:` 行应出现 `-mpc-relative-literal-loads`，这就是"根因已消除"的判据。

### 9.19.7 验证判据（离线即可判定，不必等真机）

```bash
python port/audit_scan_loop.py <新 artifact>/wlan_drv_gen4m.ko   # 必须 GOOD，退出码 0
python port/disasm_capstone.py scanGetCurrentEssChnlList <ko> --lo <loop> --hi <loop+0x40>
```
真机侧复现步骤仍是 §9.18.7（`svc wifi disable` → 观察 `main_thread` 是否还在满核空转；
`echo l > /proc/sysrq-trigger` 看 PC 是否还落在 `scanGetCurrentEssChnlList`）。

---

## 9.20 修复验证 + workaround 摘除 + 残留缺口（2026-10-06）✅ 死锁已消除

§9.19 的两处改动（`-mpc-relative-literal-loads` + drain 屏障）与 §9.20 的 workaround 摘除，
已全部落地并在真机上验证。

### 9.20.1 构建侧结果（run `37410449753` full / `37412241394` fast lane）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| `wlan_drv_gen4m.ko` ADRP(275/276) | **28021** | **0** |
| `wmt_drv.ko` ADRP | 9217 | **0** |
| `wmt_chrdev_wifi.ko` ADRP | 197 | **0** |
| `bt_drv.ko` ADRP | 161 | **0** |
| `wlan_drv_gen4m.ko` 体积 | 5 309 208 B | **3 913 696 B** |
| `wmt_drv.ko` 体积 | 1 866 592 B | **1 388 312 B** |
| Gate #6（drain 循环出口） | —（不存在） | **PASS** |

ADRP 归零的意义：产出物**不再需要**内核侧 §8.3 的 `patch_adrp_reloc.py` 手术；
该补丁保留为安全网（无 ADRP 时是空操作）。

`scanGetCurrentEssChnlList` 反汇编（新出货字节，Capstone）：

```
0x114d50  sub  x0, x0, x3          ; ← 循环头，每趟重算
0x114d54  add  x0, x0, x4
0x114d58  ldp  x2, x1, [x0, #0x10]
0x114d5c  cbz  x2, …
0x114d60  str  x1, [x2, #8]
0x114d64  cbz  x1, …
0x114d68  str  x2, [x1]
0x114d6c  ldr  w1, [x25, #0x10]    ; u4NumElem
0x114d70  stp  xzr, xzr, [x0, #0x10]
0x114d74  sub  w1, w1, #1
0x114d78  str  w1, [x25, #0x10]
0x114d7c  ldr  x0, [x25]           ; ★ 重载 head->prNext
0x114d80  cmp  x0, x25             ; ★ 判定
0x114d84  b.ne 0x114d50            ; ★ 条件回边
```

与 agui 参考版结构一致（`f9400321` vs 我们的 `f9400320`，仅寄存器分配不同）；
`port/audit_scan_loop.py` 判 **GOOD**。

### 9.20.2 真机验证（Wi-Fi 版设备，模块装入 `/vendor/lib/modules` 后重启）

| 检查 | 结果 |
| --- | --- |
| 四个模块自动加载 | ✅ `wmt_drv` / `wmt_chrdev_wifi` / `wlan_mt6761_axi` / `bt_drv` |
| `wlan0` UP + 关联 + 拿 IP | ✅ 192.168.1.63 / .64（多次重启） |
| ping 网关 + 公网 | ✅ 3/3、3/3，rtt ≈ 21 ms / 47 ms |
| **`svc wifi disable`** | ✅ **0.70 s 返回**（旧版 100% 死锁）；`Wifi HAL stopped` |
| `main_thread` | ✅ 干净退出（旧版 state=R 满核空转） |
| **`svc wifi enable`** | ✅ 0.69 s 返回；`Wifi HAL started` + `Configured chip in mode 0` |
| 点亮屏幕后重新关联 | ✅ 10 s 内拿到 192.168.1.65 |
| **`scanGetCurrentEssChnlList` 实跑** | ✅ dmesg：`Find Xiaomi_1202 in 21 BSSes, result 1` |
| `scnEventScanDone` → `aisFsmRunEventScanDone` | ✅ 状态 0 完整跑完（**旧版就是死在这里**） |

### 9.20.3 摘除 2c-2 / 2c-3 workaround

§9.16 的两个 workaround 现在**默认不生效**（`ARCHYTAS_SUSPEND_WORKAROUND=1` 可恢复），与 agui 的零 workaround 参考版一致。
理由（已在脚本注释与提交信息里留证）：

- 它们当初要治的 `kalIoctlByBssIdx: wait main_thread timeout, duration:30720ms`，**是** §9.19 那个 drain 死循环的**症状**（main_thread 卡在 drain 里，任何等它的命令都超时），不是独立的 suspend/POWERDOWN bug。
- 2c-2 把 `priv_driver_set_suspend_mode()` 变成裸 `return 0;`，让 wpa_supplicant 启用 Wi-Fi 后立刻发的
  `SETSUSPENDMODE 1` 完全空转。
- **A/B 实测**：带 workaround / 不带 workaround 两种模块，`wificond: NL80211_CMD_START_SCHED_SCAN failed: Invalid argument`
  都会出现 —— 所以 PNO 失败**与 workaround 无关**（该假设已被证伪）。

### 9.20.4 残留缺口（与死锁无关、不阻塞 Wi-Fi）：息屏时 PNO 报 EINVAL

现象：`svc wifi disable` → `enable` 之后，**若屏幕处于 Dozing（`mWakefulness=Dozing`、`mGlobalDisplayState=OFF`）**，
框架只能靠 PNO/scheduled scan 找网，而此时驱动返回：

```
wificond: NL80211_CMD_START_SCHED_SCAN failed: Invalid argument
wificond: Failed to start pno scan
```

→ 一直不重新关联；**点亮屏幕（`input keyevent KEYCODE_WAKEUP`）后 10 s 内立刻重连**，
因为亮屏时框架走普通扫描，而普通扫描一路正常（`[SCN:100:K2D] Scan flags=0x0` →
`scnFsmSteps [IDLE]->[SCANNING]` → `scnEventScanDone` → `scanGetCurrentEssChnlList` 正常返回）。

已定位到确切的返回点（Capstone 反汇编 + 源码对照）：

```c
/* os/linux/gl_cfg80211.c: mtk_cfg80211_sched_scan_start() */
ucBssIndex = wlanGetBssIdx(ndev);
if (!IS_BSS_INDEX_AIS(prGlueInfo->prAdapter, ucBssIndex))   /* :1812  (_BssIndex < KAL_AIS_NUM) */
	return -EINVAL;                                          /* 唯一没有 DBGLOG 的出口 */
```

反汇编对应 `0xa67a0: and w0,w0,#0xff / cmp w0,#1 / b.hi 0xa6b68`，而
`0xa6b68: mov w19, #-0x16` = **-22 (EINVAL)**。
即：PNO 请求落在了一个 `netdev_priv()->ucBssIdx >= KAL_AIS_NUM(=2)` 的接口上
（`wlanGetBssIdx()` 直接返回该字段）。dmesg 里也没有任何 `SCHED_SCAN_REQ_START_K2D`
或 `No match sets` 记录，正好印证"卡在第一条静默检查"。

**顺带记一个上游源码缺陷**（本项目不依赖它，但值得提）：该函数在 `prGlueInfo`
赋值**之前**就用 `prGlueInfo->prAdapter`（第 3590 行 vs 第 3609 行）。此处侥幸无害，
只因 `IS_BSS_INDEX_AIS` 展开后不使用 `_prAdapter`。

**下一步（可选，独立课题）**：确认 PNO 是从哪个 netdev 发下来的（wlan1 / p2p0？），
以及该 netdev 的 `ucBssIdx` 为何 ≥2；再决定是修正接口↔BSS 映射，还是让 `mtk_cfg80211_sched_scan_start`
接受非 AIS 接口（厂商实现可能本就不支持在那些接口上 PNO）。

## 9.21 vendor 分区 64 位适配 + 整合包（2026-10-06）✅

目标：让 **Archytas（Wi-Fi 版）** 从「64 位内核 + 32 位原厂 ROM」升级为**完整 64 位
Android**（arm64 GSI）。参考 agui 的 4G 整合包，但**基座换成 Wi-Fi 原厂 vendor**
并做三处关键改进。

### 9.21.1 基座：为什么用救砖包里的 vendor，而不是手里的 Wi-Fi 原包

| 来源 | 指纹 | 结论 |
| --- | --- | --- |
| `柴提全原无数据WiFi.9.1638/.../vendor.bin` | `Archytas:9/.../1638` | 比设备**旧** |
| `柴提全原无数据4G_9.1864/4G原包/vendor.bin` | `Archimedes:9/.../1864` | 4G 版 |
| `mtkclient-archytas-一键救砖包.zip → img/vendor.img` | `Archytas:9/.../**1754**`，`date.utc=1610044705` | ✅ **与设备 `getprop` 完全一致** |

设备当前 `ro.vendor.build.fingerprint = Xiaomi/full_Archytas/Archytas:9/PPR1.180610.011/1754`。
用 1754 救砖镜像做基座，避免把 4G 的 SIM/蜂窝/按键配置带进来。

> **纠正一个误判**：agui vendor 里有 88 个文件与我们的 4G 原包「大小不同」，
> 但比对 `build.prop` 后确认**只是 ROM 版本差异** —— 他的基座是
> `Archimedes:9/.../462:userdebug`（2019-04-26 构建），不是我们的 1864。
> 真正刻意的改动只有 53 项**新增**。

### 9.21.2 三处改动（`port/device_make_vendor64.sh` + `port/build_wifi64_vendor.py`）

**① 注入 `/lib64/`（29 个 aarch64 库，11,640,469 B）**

64 位 SurfaceFlinger / 应用需要**同位数（进程内 dlopen）**的 vendor 库。原厂
vendor **完全没有 `/vendor/lib64`**（全 32 位 ELF），这才是 arm64 GSI 跑不起来的根因。

- GPU/PowerVR：`libusc` `libsrv_um` `libglslcompiler` `libIMGegl` `libgpu_aux` `libged` `libladder` `libbwc` `libtqvalidate`
- EGL/GLES：`egl/libEGL_mtk` `libGLESv1_CM_mtk` `libGLESv2_mtk` `egl.cfg`
- 显示/合成：`hw/gralloc.mt6761` `hw/hwcomposer.mt6761` `hw/memtrack.mt6761` `hw/gralloc.default` `hw/android.hardware.graphics.mapper@2.0-impl`
- 内存/ION：`libion_mtk` `libion_ulit` `libgralloc_extra` `libdrm` `libudf`
- 画质 PQ：`libdpframework` `libpq_prot` `libpq_cust_base` `vendor.mediatek.hardware.pq@2.0/2.1/2.2`

来源＝agui 的 4G vendor（同 SoC/同 MT6761/同 vendor 树），**逐个校验 `ELF64 + EM_AARCH64`** 后搬运。

**② 替换 `/lib/modules/*.ko` 为本仓库的 arm64 模块**

`wmt_drv` / `wmt_chrdev_wifi` / `wlan_drv_gen4m` / `bt_drv`，取自 CI run `37412241394`
（含 §9.19 的 drain 死循环修复 + §9.20 的 workaround 摘除）。
原厂是同名 **ELF32 ARMv7** 模块，64 位内核 `insmod` 必失败。

**③ `/default.prop`：`ro.zygote=zygote32` → `zygote64_32`**

**不是可选项。** 实测两份 GSI（agui 的 2.00 GiB 与极简 1.396 GiB）的
`/system/etc/prop.default` 与 `/system/build.prop` **都没有定义 `ro.zygote`**；
设备上该属性唯一来源就是 `/vendor/default.prop`，而 `ro.` 属性只写一次。
不改则 arm64 GSI 只会拉起 32 位 zygote。

### 9.21.3 已核实「不需要改」的项（避免过度改动）

| 项 | 结论与依据 |
| --- | --- |
| **SELinux 策略** | **不用改。** 原厂 `vendor_file_contexts`/`plat_file_contexts` 已为全部 29 个库写好 `same_process_hal_file` 规则（`/vendor/lib(64)?/libusc\.so`、`hw/gralloc\.mt[0-9]+\.so`、`egl(/.*)?` …），说明原厂本就为 64 位图形栈预留标签。适配时按**同名 32 位文件 `chcon` 复制上下文**即可（已验证结果：`same_process_hal_file` / `vendor_file` / `vendor_hal_file` 与原 32 位一致） |
| **`/etc/init/*.rc`** | **不用改。** agui 的 `init.wlan_drv.rc` 差异只是末尾多了个 **`disabled`** 的自用 bootguard 服务（`start` 还是注释掉的），与位数无关 |
| **`ro.product.cpu.abilist`** | **不用管。** vendor 只设 `ro.vendor.product.cpu.*`；面向应用的 `ro.product.cpu.abilist` 由 GSI 提供（`arm64-v8a,armeabi-v7a,armeabi`），二者不冲突 |
| **VNDK** | **天然匹配。** vendor `ro.vndk.version=28`；两份候选 GSI 都恰好携带 `vndk-28`(180)+`vndk-sp-28`(33)。**换 Android 10 的 GSI 反而会因为缺 vndk-28 而挂** |
| **lk / preloader** | **不动。** 原厂 lk + 本包 boot 已实测可启动 |

### 9.21.4 system（GSI）选型：极简包 vs agui 包

| | agui `system-arm64-ab-vanilla-nosu.img` | `极简arm64gsi可不扩容刷入.zip` |
| --- | --- | --- |
| 构建 | `treble_arm64_bvN 9 PQ3B.190801.002`（phh） | **同一构建** |
| ext4 声明尺寸 | **2.00 GiB** | **1.396 GiB**（366000 × 4096） |
| 能否放进 system 分区（1,503,232,000 B） | ❌ 需扩容分区 | ✅ **余 4,096,000 B** |
| vndk | vndk-28 / vndk-sp-28 | 同 |

→ **选极简包**。agui 包里那份必须走 `expand_system_*` 扩容，风险高且多余。

### 9.21.5 构建方式：为什么在**设备上**做

Windows 主机**没有**可用的 ext4 写工具（无 WSL、无 e2fsprogs），而设备自带完整
e2fsprogs（`mke2fs`/`e2fsck`/`tune2fs`/`resize2fs`）+ `losetup`。于是：

```
cp 一份原厂镜像 -> losetup /dev/block/loop0 -> mount -t ext4 -o rw
  -> 注入 lib64 / 换模块 / 改 default.prop / chcon
  -> umount -> e2fsck -fy
```

**不动线上 `/vendor`**（已核实产出后线上仍无 `/vendor/lib64`），产出的是干净可 flash 的镜像。
本地校验（`port/build_wifi64_vendor.py`）11 项全过：文件数、模块 sha256、
`ro.zygote`、`ro.vndk.version`、基座版本号、注入库全部 ELF64。

**踩坑**：`getenforce` 在 Android 上返回的是 `Enforcing`/`Permissive` **文本**（不是 `1`/`0`），
首版脚本拿它和 `"1"` 比较导致**改完标签后没把 SELinux 恢复回 Enforcing**（设备一度停在
Permissive）。已改用 `case` 匹配并立即 `setenforce 1` 复原。

### 9.21.6 真机内核回读验证（只读，不动分区）

把**最终** `vendor.img` 推到设备 → `losetup` + `mount -ro` 用**真实内核**读回：

| 检查 | 结果 |
| --- | --- |
| `/vendor/lib64` 内容 | 29 个文件，`ls -Z` 标签正确 |
| `default.prop` | `ro.zygote=zygote64_32` ✅ |
| 4 个模块 md5 vs 主机 arm64 构建 | **逐字节相同** ✅ |
| 抽样 `.so` ELF 头 | `7f454c4602`（ELF64）✅ |
| `e2fsck -fy` | `982/101632 files, 44272/101575 blocks`，干净 |

### 9.21.7 交付物

`WiFi版64位整合包/`（1.9 GB，另出同名 zip）：

```
img/boot.img      64 位内核 4.9.117 + Wi-Fi DTB（consys 修复）  sha256 18d8a3c5…
img/vendor.img    ★ 64 位适配版 vendor（1754 + lib64 + arm64 模块） sha256 41a60cb0…
img/system.img    phh treble_arm64_bvN 9，raw 1,499,136,000 B    sha256 552ced84…
stock/dtbo.img    原厂 overlay 1754（Wi-Fi，无 SIM GPIO）
stock/lk.bin lk2.bin vbmeta.img boot-stock-1754.img MT6761_Android_scatter.txt
flash_wifi64.sh   一键刷机（备份→写 4 分区→关 AVB→清 userdata+metadata→重启）
README.md         完整说明（含与 agui 4G 包的逐项对比）
MANIFEST.sha256
```

**输入文件（在仓库外，需自备，不入版本控制）**：

| 路径 | 说明 | 获取方式 |
| --- | --- | --- |
| `D:/Downloads/_archytas_rom/vendor_rescue.img` | 1754 原厂 vendor 基座（400 MiB） | 解压 `D:/Downloads/mtkclient-archytas-一键救砖包.zip` 取 `img/vendor.img` 重命名 |
| `D:/Downloads/极简arm64gsi可不扩容刷入.zip` | system（arm64 GSI 9，raw 1,499,136,000 B） | 第三方重打包件 |
| `D:/Downloads/4g小爱64位系统整合包/` | `vendor/lib64` 的 29 个 aarch64 库来源 | agui 交付的 4G 整理包 |
| `_ci/vendor_audit/agui_lib64/`、`_ci/37412241394/` | 上两项抽取到本地的暂存（`_ci/` 已忽略） | 见 §9.21.9 脚本 |

`build_wifi64_vendor.py` 的基座默认值即 `os.path.dirname(ROOT)/_archytas_rom/vendor_rescue.img`，
`--base/--lib64/--ko/--out` 均可覆盖。

### 9.21.8 arm64 GSI 实机启动 —— **已于同日完成，见 §9.22**

- 结论：**走 fastboot 即可，不必进 BROM**。`lk` 自带 fastboot，`adb reboot bootloader`
  可进，`fastboot flash system` 对 system-as-root 的分区同样有效。
- **换 system 必须清 `userdata` + `metadata`**（原 data 属 Android 10/32 位，
  新 system 是 Android 9/arm64）。vendor fstab 里 `userdata`/`cache` 带 `formattable`，
  擦掉后开机自动重建，不需要手动 `mke2fs`。

### 9.21.9 新增工具

- `port/ext4_inventory.py` —— 整树清点 + 双镜像 diff（旧 `tree`/`find` 每级从根重走，O(n²) 不可用）
- `port/ext4_extract.py` —— 从镜像里递归提取子树（保留权限/软链）
- `port/sparse.py` —— Android sparse 镜像只读适配器（可直接喂给 `ext4_read.Ext4`）+ `expand` 子命令
- `port/device_make_vendor64.sh` —— 设备端 vendor 改造脚本
- `port/build_wifi64_vendor.py` —— 主机驱动：推送 → 设备端构建 → 拉取 → **12 项校验**
- `port/elf_integrity.py` —— ELF **结构完整性**门禁（2026-10-06 增，见 §9.22）：
  要求 `e_phoff+e_phnum*e_phentsize`、`e_shoff+e_shnum*e_shentsize`、以及每个
  `PT_LOAD`/`PT_DYNAMIC` 的 `p_offset+p_filesz` 都不越过文件尾。只比对 ELF magic/class
  是**不够**的——被截断的文件头依然完好。
- `ext4_read.py` 增补：`Ext4()` 现在也接受**类文件对象**（配合 sparse 适配器）；
  **并按 `ee_block` 定位 extent、hole 补零**（见 §9.22，这是个真实事故）
- `port/ci_push.py` —— 顺带解决的老问题：本机 `credential.helper=helper-selector` 是**交互式**的，
  非交互 shell 里 `git push` 会**永久挂住**。该脚本复用 `ci_auth.py` 的取值逻辑，
  把 token 拼进一次性 URL 并先 `-c credential.helper=` 清空 helper 列表，
  同时对输出做脱敏（`user:token@github.com` → `github.com`），因此回调也不泄密。
- `WiFi版64位整合包/flash_wifi64_fastboot.sh` —— fastboot 版一键刷机（跳过 dtbo/vbmeta）

---

## 9.22 实机刷入 + 一个把 19 个 .so 截断的 ext4 读取器 bug（2026-10-06）✅

### 9.22.1 走 fastboot，不动 dtbo / vbmeta（先验证再动手）

刷之前把三件事查清楚，避免了两次无谓改动：

| 检查 | 做法 | 结论 |
| --- | --- | --- |
| bootloader | `fastboot getvar unlocked` | `yes`，`ro.boot.flash.locked=0` |
| **dm-verity 是否在跑** | `mount \| grep vendor` | `/vendor` 由 `/dev/block/mmcblk0p30` **直挂 ext4**，不是 `/dev/block/dm-X`；`ro.boot.veritymode` 为空 → **verity 未启用，vbmeta 完全不用动** |
| dtbo 是否需要刷 | 拉下设备 dtbo 与包内比对 | 设备 dtbo 的**前 8,388,579 字节与包内逐字节相同**，多出的 29 字节全零 → **不用刷** |

> 另一条捷径：**不需要 BROM**。`lk` 自带 fastboot，`adb reboot bootloader` 即可；
> `fastboot flash system` 对 system-as-root 的分区照样有效。`max-download-size=0x8000000`
> （128 MiB），1.4 GB 的 system 由 fastboot **自动重编码为 sparse 分块**发送
> （11 块 / 106.9 s），400 MiB 的 vendor 分 2 块。
> 分块后**分区内容经复核与权威副本逐字节一致**（29/29），没丢数据。

### 9.22.2 症状：卡在第一屏，但 adb 通

刷完首启卡在**第一屏**，但 `adb devices` 有设备——说明**内核已起**（不是内核挂住）：

```
ro.product.cpu.abi   = arm64-v8a     ✅  Zygote 位数已切换
ro.zygote            = zygote64_32   ✅  vendor 适配三处改动全部生效
init.svc.zygote            = restarting        ← 崩溃循环
init.svc.surfaceflinger    = restarting        ← 崩溃循环
```

崩溃缓冲给出确切死因：

```
Abort message: 'couldn't find an OpenGL ES implementation'
  #02 /system/lib64/libEGL.so (android::Loader::open+724)
  #03 /system/lib64/libEGL.so (egl_init_drivers+100)
  #05 /system/lib64/libsurfaceflinger.so (RenderEngine::create+60)
  #06 /system/lib64/libsurfaceflinger.so (SurfaceFlinger::init+832)
E libEGL : load_driver(/vendor/lib64/egl/libEGL_mtk.so): unknown
```

关键线索是 **`unknown`**：`dlopen()` 真的失败时 `dlerror()` 应当给出原因；
返回 NULL 说明这是"依赖库本身有问题"，bionic 给不出错误串。

### 9.22.3 根因：`ext4_read.py` 丢掉 `ee_block`，把稀疏 hole 塌陷了

排除了两个显而易见的嫌疑后锁定读取器：

- **不是 SELinux**：`getenforce=Enforcing`，但 `dmesg | grep avc` 里**没有一条**
  与 libEGL / `/vendor/lib64` 相关（只有 `nvram_agent_binder` 的 binder 权限，与图形无关）；
- **不是标签**：`ls -lZ` 显示 `/vendor/lib64/egl/*`、`libusc.so` 等全是
  `u:object_r:same_process_hal_file:s0`，与 32 位同名文件一致 —— §9.21.3 的判断是对的。

用**设备内核**（`losetup` + `mount -ro` 挂载 agui 原包）拿到权威尺寸，与我们的提取结果对比：

| 文件 | 我们读到 | **权威** |
| --- | --- | --- |
| `libged.so` | 32,768 | **68,392** |
| `libion_ulit.so` | 12,288 | **68,104** |
| `libgralloc_extra.so` | 16,384 | **68,312** |
| `libdpframework.so` | 745,472 | **794,840** |
| `hw/hwcomposer.mt6761.so` | 643,072 | **662,072** |

**19 / 29 个库被截断**。解码 `libged.so` 的 inode 立刻看清原因：

```
eh_magic=0xf30a entries=2 max=4 depth=0
  slot 0: ee_block=0    ee_len=6  start=34703
  slot 1: ee_block=15   ee_len=2  start=34709   ← 逻辑块号从 0 跳到 15！
```

两个 extent 之间**有一个 9 块的 hole**。这不是异常数据，而是 Android 64 位 `.so`
**按 64 KiB 页对齐**的正常布局：两个 `PT_LOAD` 段之间的间隙本来就是全零（稀疏）。

而旧代码是：

```python
data = i_block[12:]                       # i_block 只有 60 字节 → 只剩 48 = 4 条记录
...
yield (start, length)                     # ← ee_block 被丢掉了
...
blocks.extend(range(start, start + length))   # 按物理顺序直接拼
```

`file_blocks()` / `_read_any()` 拿到的是"物理块序列"，**没有逻辑位置信息**，
于是 hole 被"塌陷"：68,392 字节的文件被写成 32,768 字节，而且第二个 extent 的数据
还放错了位置（落在 24,576 而不是 61,440）。ELF 节头表因此跑到文件尾之外。

### 9.22.4 为什么这个 bug 能一路穿过所有校验

`build_wifi64_vendor.py` 原来的"每个库都是 ELF64"检查是：

```python
data = fs._read_any(i3, inode)[:20]       # 只读前 20 字节
if data[:4] == b"\x7fELF" and data[4] != 2:
    bad.append(...)
```

**截断的文件头依然完好** —— `\x7fELF` + `EI_CLASS=2` 一个字节不差。头 20 字节检查
永远发现不了尾部丢失。`e2fsck` 也不管这个（文件系统是合法的，是**文件内容**错了）。

### 9.22.5 修复（两处，2026-10-06）

**① `port/ext4_read.py` —— 按逻辑块定位，hole 补零**

```python
def _extent_blocks(self, block):
    """Yield (logical_block, start_block, count, initialized)."""
    ...
    if ee_len <= 32768:                    # 内核语义：<=32768 是已初始化
        length, initialized = ee_len, True
    else:                                  # >32768 是未初始化 extent，内容读作 0
        length, initialized = ee_len - 32768, False
    yield (ee_block, start, length, initialized)

def read_inode_data(self, inode):
    buf = bytearray(nblocks * bs)          # hole 保持全零
    ...
    buf[lblk*bs : lblk*bs + len(chunk)] = chunk     # 按逻辑块号落位
    return bytes(buf[:size])
```

顺带修的：`ee_len == 32768` 原来被 `& 0x7FFF` 算成 0（现在正确）；fast symlink
（目标存在 `i_block` 里、没有 extent 头）原来会直接抛异常，现在正确返回。

**② 新增 `port/elf_integrity.py` —— 结构完整性门禁**（已接进 `build_wifi64_vendor.py`）

要求程序头表、节头表、以及每个 `PT_LOAD`/`PT_DYNAMIC` 都在文件范围内。
实测：**旧的坏副本 19/28 被抓出，修复后的 0/28**。

### 9.22.6 验证（三重）

1. **交叉验证读取器**：用修好的 `ext4_extract.py` 从 agui 原包重新提取，
   与设备内核读出的权威副本比对 → **29/29 逐字节一致**（12,387,861 B，旧的是 11,640,469 B）。
2. **重建 + 重刷**：vendor 只重刷 vendor 分区（**不需要再清数据**），
   设备分区上的 lib64 与权威副本 → **29/29 逐字节一致**。
3. **实机启动**：`sys.boot_completed=1`，约 30 秒进桌面。

| 检查项 | 结果 |
| --- | --- |
| `ro.product.cpu.abi` / `ro.zygote` | `arm64-v8a` / `zygote64_32` ✅ |
| `abilist` / `abilist64` | `arm64-v8a,armeabi-v7a,armeabi` / `arm64-v8a` |
| 64 位进程 | `zygote64` `zygote` `surfaceflinger` `webview_zygote` |
| `surfaceflinger` / `zygote` / `zygote_secondary` | `running` / `running` / `running`（原为 `restarting`） |
| `bootanim` | `stopped`（桌面已起，已截屏确认） |
| 内核模块 | `wlan_mt6761_axi` `wmt_chrdev_wifi` `bt_drv` `wmt_drv` 全部 insmod 成功 |
| WMT | `[HIF-SDIO] mtk_wcn_wmt_func_ctrl: ... ok`、`BT_open: WMT turn on BT OK!` |
| Wi-Fi 开/关 | 开→`Wi-Fi is enabled`；关→正常；**无满核卡死**（§9.19 修复生效） |
| SELinux / panic | `Enforcing` / 无 panic |
| 崩溃缓冲 | SurfaceFlinger 与 libEGL 的崩溃已消失 |

### 9.22.7 遗留

- `nvram_agent_binder` 仍在崩溃循环（**不影响开机与 Wi-Fi**）：phh GSI 的策略里
  `nvram_agent_binder` 域无权读写 `/dev/binder`（`scontext=u:r:nvram_agent_binder:s0`
  `tcontext=u:object_r:binder_device:s0` `tclass=chr_file`）。属 GSI 策略与 MTK vendor
  预期间的缺口，与本包改动无关。需要用该服务（NVRAM 相关特性）时补一条 `allow` 即可。
- `fpsgo.ko` / `met.ko` 仍为 32 位（同 agui 包），只影响调频。

### 9.22.8 教训（值得复用到任何镜像操作）

1. **比对尺寸要用可信来源**。Windows 上没有 ext4 用户态工具，**设备自带的内核就是
   最好的 oracle**：`losetup` + `mount -ro`。靠"看起来能解析"来自证是不可靠的。
2. **"文件头合法"≠"文件完整"**。凡是要搬运二进制（.so/.ko/固件），门禁必须查**尾部**。
3. **稀疏文件是常态，不是例外**。ext4 的 `ee_block`、uninit extent、ELF 的页对齐 hole ——
   任何"按物理顺序拼接"的实现都会在这些地方出错。
4. **`dlerror()` 返回 NULL 时要往"依赖库损坏"方向查**，不要只盯着权限和命名空间。

---

*生成/修订于 2026-10-02。工具链：自写 `dtb_dump.py`（反编译）、`dtb_diff.py`（全量 diff）、`make_wifi_template.py`（DTB 替换）、`analyze_oem_diff.py`（原厂对比）、`patch_wifi_dtb.py`（DTB 最小手术，2026-10-04 增）、`elf_symbols.py`（模块符号检查）、`build_connectivity_modules.sh`（2026-10-04 重写）、`verify_build_script.py`（Gate #1-6）、`install_wifi_modules.sh`（vendor 分区自动加载）、`extract_modules.py`（裸 ext4 提 .ko，2026-10-06 增）、`audit_wmt_fb_workaround.py` + `disasm_arm64.py`（workaround 生效审计，2026-10-06 增）、`audit_scan_loop.py`（drain 循环出口门禁，2026-10-06 增）、`disasm_capstone.py`（Capstone 权威反汇编，2026-10-06 增）、`diff_func_insns.py`（两个构建的同函数逐指令 diff）、`ext4_inventory.py` / `ext4_extract.py` / `sparse.py` / `device_make_vendor64.sh` / `build_wifi64_vendor.py`（vendor 64 位适配链，2026-10-06 增）、`elf_integrity.py`（ELF 结构完整性门禁：头表/段必须都在文件内，2026-10-06 增）、`ci_push.py`（绕过交互式 credential helper 的推送，2026-10-06 增）。权威证据：4G/ Wi-Fi 双方原厂 `boot.bin`+`dtbo.bin` 抽出的 DTB 与 `diff_main_oem.txt`/`diff_dtbo_oem.txt`；Archytas 全量原厂镜像（救砖包 `img/`）；**设备内核经 `losetup`+`mount` 读出的 `/vendor/lib64` 权威副本**（§9.22 用它定案）。*
