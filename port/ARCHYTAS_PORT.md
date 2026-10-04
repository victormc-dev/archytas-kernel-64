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

---

*生成/修订于 2026-10-02。工具链：自写 `dtb_dump.py`（反编译）、`dtb_diff.py`（全量 diff）、`make_wifi_template.py`（DTB 替换）、`analyze_oem_diff.py`（原厂对比）、`patch_wifi_dtb.py`（DTB 最小手术，2026-10-04 增）。权威证据：4G/ Wi-Fi 双方原厂 `boot.bin`+`dtbo.bin` 抽出的 DTB 与 `diff_main_oem.txt`/`diff_dtbo_oem.txt`。*
