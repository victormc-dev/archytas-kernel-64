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

### 8.6 本地校验工具

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

---

*生成/修订于 2026-10-02。工具链：自写 `dtb_dump.py`（反编译）、`dtb_diff.py`（全量 diff）、`make_wifi_template.py`（DTB 替换）、`analyze_oem_diff.py`（原厂对比）。权威证据：4G/ Wi-Fi 双方原厂 `boot.bin`+`dtbo.bin` 抽出的 DTB 与 `diff_main_oem.txt`/`diff_dtbo_oem.txt`。*
