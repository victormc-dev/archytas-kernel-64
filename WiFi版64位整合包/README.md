# 小爱老师 Wi-Fi 版（Archytas）64 位 ROM 整合包

> 把 **Archytas（小爱老师 Wi-Fi 版）** 从「32 位原厂 ROM + 64 位内核」升级为
> **完整 64 位 Android（arm64 GSI）**，包含本仓库自行完成的 **vendor 分区 64 位适配**。
>
> 参考物：`agui`（阿龟）的 **4G 版** 整合包 —— 本包沿用其已被验证的思路
> （vendor 注入 `/lib64` 图形栈 + `ro.zygote=zygote64_32`），但
> **基座换成 Wi-Fi 原厂 vendor**，并做了三处关键改进（见下文）。

---

## 一、包里有什么

| 路径 | 内容 | 说明 |
|---|---|---|
| `img/boot.img` | 64 位内核 boot | Linux **4.9.117** aarch64 + **Wi-Fi 板 DTB**（含 consys reserved-memory 修复），32 MiB / Android v1 / 2048 页 |
| `img/vendor.img` | **64 位适配版 vendor** | ★ 本包核心，见第二节 |
| `img/system.img` | arm64 GSI | phh `treble_arm64_bvN` Android 9（`PQ3B.190801.002`），**1.396 GiB，无需扩容 system 分区** |
| `stock/dtbo.img` | Wi-Fi 原厂 overlay | build 1754，含正确的「无 SIM 卡槽」配置 |
| `stock/lk.bin` `lk2.bin` | 原厂引导 | **仅供回退**，正常刷机不要动 |
| `stock/vbmeta.img` | 原厂 vbmeta | 仅供回退 |
| `stock/boot-stock-1754.img` | 原厂 boot | 仅供回退 |
| `stock/MT6761_Android_scatter.txt` | 分区表 | 查分区名/大小 |
| `flash_wifi64.sh` | 一键刷机 | 见第四节 |

**不需要扩容任何分区**：GSI 已按原机 system 分区尺寸（1,503,232,000 B）重新打包为
1,499,136,000 B。agui 的 4G 包用的是同一构建的原始镜像（声明 **2.00 GiB**），
必须扩容 system 分区才能刷；本包不需要。

---

## 二、vendor 适配做了什么（本包与 4G 包的核心差异）

### 基座

用 **Wi-Fi 原厂 vendor（build 1754，400 MiB ext4）**，而非 4G 的。
理由：设备当前的 `ro.vendor.build.fingerprint` 就是
`Xiaomi/full_Archytas/Archytas:9/PPR1.180610.011/1754`（`date.utc=1610044705`），
用同版本原厂镜像做基座，避免把 4G 的 SIM/蜂窝/按键配置带进来。

> 顺带纠正一个误判：agui 的 vendor 里有 88 个文件与我们的 4G 原包「大小不同」，
> 但那些**只是他的 ROM 版本差异**（他的基座是 `Archimedes:9/.../462:userdebug`，
> 2019 年构建），并非刻意修改。真正刻意的改动只有 53 项新增。

### 三处改动

**① 注入 `/lib64/`（29 个 aarch64 库，11.6 MB）**

64 位 SurfaceFlinger / 应用需要**同位数（进程内加载）**的 vendor 库，否则
gralloc mapper、ion、GPU 用户态驱动都无法 dlopen。原厂 vendor 里**完全没有
`/vendor/lib64`**（全 32 位），这就是 arm64 GSI 跑不起来的根因。

| 组 | 文件 |
|---|---|
| GPU（PowerVR） | `libusc.so` `libsrv_um.so` `libglslcompiler.so` `libIMGegl.so` `libgpu_aux.so` `libged.so` `libladder.so` `libbwc.so` `libtqvalidate.so` |
| EGL/GLES | `egl/libEGL_mtk.so` `egl/libGLESv1_CM_mtk.so` `egl/libGLESv2_mtk.so` `egl/egl.cfg` |
| 显示/合成 | `hw/gralloc.mt6761.so` `hw/hwcomposer.mt6761.so` `hw/memtrack.mt6761.so` `hw/gralloc.default.so` `hw/android.hardware.graphics.mapper@2.0-impl.so` |
| 内存/ION | `libion_mtk.so` `libion_ulit.so` `libgralloc_extra.so` `libdrm.so` `libudf.so` |
| 画质 PQ | `libdpframework.so` `libpq_prot.so` `libpq_cust_base.so` `vendor.mediatek.hardware.pq@2.0/2.1/2.2.so` |

来源：agui 的 4G vendor（同 SoC / 同 MT6761 / 同 vendor 树，逐个校验为
`ELF64 + EM_AARCH64` 后原样搬运）。

**② 替换 `/lib/modules/*.ko` 为本仓库构建的 arm64 模块**

| 模块 | 作用 |
|---|---|
| `wmt_drv.ko` `wmt_chrdev_wifi.ko` `wlan_drv_gen4m.ko` `bt_drv.ko` | Wi-Fi / 蓝牙连通性 |

原厂模块是 **ELF32 ARMv7**，在 64 位内核上 `insmod` 必然失败。这里用的是本仓库
CI 最新构建（run `37412241394`），含两项修复：
- **代码生成修复**：`-mpc-relative-literal-loads` 缺失曾导致 GCC 11 把
  `scanGetCurrentEssChnlList` 的 drain 循环误编成死循环（`svc wifi disable` → 满核卡死）；
- **workaround 摘除**：2c-2/2c-3 两个 suspend 补丁改为 opt-in 默认关。

> `fpsgo.ko` / `met.ko` 仍是 32 位（MTK 性能调频模块，加载失败只影响调频，不影响功能），
> agui 的包也同样保持 32 位。

**③ `/default.prop`：`ro.zygote=zygote32` → `zygote64_32`**

这一处**不是可选项**。实测：GSI 的 `/system/etc/prop.default` 与
`/system/build.prop` **都没有定义 `ro.zygote`**，设备上该属性的唯一来源就是
`/vendor/default.prop`；而 `ro.` 属性只写一次、后加载者不生效 —— 若仍是
`zygote32`，arm64 GSI 只会拉起 32 位 zygote。

### 不需要改的东西（已核实，别多改）

| 项目 | 结论 |
|---|---|
| **SELinux 策略** | **不用改**。原厂 `vendor_file_contexts` / `plat_file_contexts` 里**已经**为这 29 个库写好了 `same_process_hal_file` 规则（`/vendor/lib(64)?/libusc\.so`、`gralloc\.mt[0-9]+\.so`、`egl(/.*)?` …），说明原厂本就为 64 位图形栈预留了标签。适配时按同名 32 位文件 `chcon` 逐一复制上下文即可 |
| **`/etc/init/*.rc`** | **不用改**。agui 那份 `init.wlan_drv.rc` 的差异只是加了个 **`disabled`** 的自用 bootguard，与位数转换无关 |
| **`ro.product.cpu.abilist`** | **不用管**。vendor 只设 `ro.vendor.product.cpu.*`，面向应用的 `ro.product.cpu.abilist` 由 GSI 提供（`arm64-v8a,armeabi-v7a,armeabi`） |
| **VNDK** | **天然匹配**。vendor `ro.vndk.version=28`，该 GSI 正好携带 `vndk-28` / `vndk-sp-28`（180 + 33 个库）；换成 Android 10 的 GSI 反而会因为缺 vndk-28 而挂 |
| **lk / preloader** | **不要动**。沿用原机即可（本仓库已实测：原厂 lk + 本包 boot 可正常启动） |

---

## 三、与 agui 4G 包的对比

| 项 | agui 4G 包 | 本包（Wi-Fi） |
|---|---|---|
| vendor 基座 | 4G 原厂 `462`(2019) | **Wi-Fi 原厂 `1754`**（与设备一致） |
| `/lib64` | 有 | 有（同源，逐个校验 ELF64） |
| arm64 模块 | 有（含两处 suspend workaround、**未修 drain 死循环**） | 有（**已修死循环** + workaround 默认关） |
| `ro.zygote` | `zygote64_32` | 同 |
| system GSI | 2.00 GiB，**需扩容分区** | **1.396 GiB，不扩容** |
| dtbo | 4G 版（**带 SIM GPIO，刷到 Wi-Fi 会按键错乱**） | **Wi-Fi 原厂** |
| 蜂窝/IPsec 组件 | 有（charon/strongswan/volte/epdg…） | 无（Wi-Fi 版无基带，不需要） |
| 额外杂项 | EmCamera、bootguard、`.armv7-*`/`.before-emi-hole-*` 备份 | 无 |

---

## 四、刷入

### 前置

- 设备已 **解锁 bootloader**（`ro.boot.verifiedbootstate=orange` / `flash.locked=0`）
- MTKClient 可用（`git clone https://github.com/bkerler/mtkclient` + `pip install -r requirements.txt`）
- USB 驱动：Windows 推荐 **UsbDk** 绑定 `0e8d:0003`(BROM) / `0e8d:7000`(preloader)

### 一键

```bash
# 设备关机 → 按住音量下 + 插 USB（进入下载模式）
MTK_AUTH="--auth 123456" bash flash_wifi64.sh
```

脚本会：备份 → 写 boot / dtbo / vendor / system → 关 AVB → **清 userdata + metadata** → 重启。

### 手动（等价）

```bash
mtk payload --auth 123456
mtk w boot   img/boot.img
mtk w dtbo   stock/dtbo.img
mtk w vendor img/vendor.img
mtk w system img/system.img
mtk da vbmeta 3          # 关 verity + verification（老版语法：mtk vbmeta 3）
mtk e userdata           # 必须
mtk e metadata           # 必须
mtk reset
```

> **换 system 必须清数据**：原机 data 属于 Android 10（32 位），新 system 是
> Android 9 arm64，不清会卡在开机动画/加密失败。

### 首次开机

2~5 分钟（重建 data + 加载 GSI）。点亮屏幕后自检：

```bash
adb shell getprop ro.product.cpu.abi        # arm64-v8a
adb shell getprop ro.product.cpu.abilist    # arm64-v8a,armeabi-v7a,armeabi
adb shell getprop ro.zygote                 # zygote64_32
adb shell getprop ro.product.cpu.abilist64  # arm64-v8a
adb shell ls /vendor/lib64 | wc -l          # 29
adb shell ls /vendor/lib64                  # libusc.so / libsrv_um.so / egl/ / hw/ ...
adb shell dmesg | grep -i "Find .* in .* BSSes"   # 扫描路径跑通（旧版死在这里）
```

---

## 五、风险与回退

- **不动 preloader / lk / lk2 / nvram / nvdata / protect\***，不会硬砖。
- 出问题随时回退：`stock/` 里有原厂 `boot-stock-1754.img` / `dtbo.img` / `lk.bin` / `vbmeta.img`；
  vendor / system 请用刷机前的备份（`BACKUP=1 FULL_BACKUP=1`）。
- 黑屏但能 `adb`：多为面板名（lk atag）不匹配，与本次改动无关。
- 本包内所有 `img/*` 均由脚本生成，可复现：
  - vendor：`python port/build_wifi64_vendor.py`（设备端 loop 挂载改造）
  - system：`python port/sparse.py expand <sparse> <raw>`
  - boot：GitHub Actions（`.github/workflows/build.yml`）

---

*本包由 `victormc-dev/archytas-kernel-64` 项目生成。固件/内核版权归各自所有者，仅供研究与自用设备学习。刷机有风险，操作前务必备份。*
