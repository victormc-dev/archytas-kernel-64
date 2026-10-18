# AGUI 4G vendor 对照审计 — suspend/卡死差异定位（2026-10-05）

> 目标：以 agui 做好的 **4G 版可运行 vendor**（`vendor.img`，400 MiB ext4）为 ground truth，
> 对照 Wi-Fi 版构建，核查 §9.16 的 suspend/POWERDOWN 死锁到底差在哪，并判断"DMA 问题"这条思路是否成立。
> 全部结论来自对 vendor.img 内实际 `.ko` 的符号表 / 重定位表 / init 配置的读取；可用
> [`audit_agui_vendor.py`](audit_agui_vendor.py) 一键复现。

---

## 1. 一句话结论

- **"卡死是 DMA 通道问题"当前证据不支持**：普通收发/扫描/ping 全通（§9.15），且 agui 用**同一套驱动**（零 workaround）在 4G 板上 suspend 正常。
- **文档 §9.16 的"头号嫌疑"（`kalSetSuspendFlagToEMI` 空指针写 EMI）被源码证伪**：`gConEmiPhyBase` 在 arm64 上由**内核导出**（consys `0xbf000000`，非 0），`kalSetSuspendFlagToEMI` 有提前 `return` 保护（`gl_kal.c:8884`），EMI 挂起标志供给是正常的。
- 真正差异是**我们打了 2 个 workaround（2c-2/2c-3），而 agui 4G 一个都不用打** → 建议在验证 EMI 挂起标志写入后，摘掉 workaround 恢复真 suspend，而不是把时间花在"DMA/DMA scheduler"上。

---

## 2. 证据来源（全部可复现）

| 项 | 值 |
|---|---|
| `vendor.img` `/lib/modules` | `wlan_drv_gen4m.ko`（54,288,528 B，2026-09-29 15:44）、`wlan_drv_gen4m.ko.before-emi-hole-20260927`、`wmt_drv.ko` / `wmt_chrdev_wifi.ko` / `bt_drv.ko` |
| `/etc/init` | `init.wlan_drv.rc`、`init_connectivity.rc`、`init.wmt_drv.rc`、`wlan_assistant.rc` |
| `/firmware` | `WIFI_RAM_CODE_soc1_0_1_1.bin`（576756 B）等 — 与 Wi-Fi 原厂**同名同套** |
| agui 内核 | `drivers/misc/mediatek/connectivity/common/connectivity_build_in_adapter.c`（导出 `gConEmiPhyBase/gConEmiSize`，`RESERVEDMEM_OF_DECLARE("mediatek,consys-reserve-memory")`） |
| 上游模块源码 | `wlan-core @ ba2c5a5`、`common @ 364afcf` |

---

## 3. 关键对照表

| 维度 | agui 4G vendor（ground truth） | 我们 Wi-Fi 构建 | 结论 |
|---|---|---|---|
| wlan suspend 逻辑 | **完整**：`priv_driver_set_suspend_mode` 内 `0x74214→wlanSetSuspendMode`、`0x74220→p2pSetSuspendMode` | 2c-2 替换为 `return 0` | 我们绕开 |
| wmt fb POWERDOWN | **完整**：`wmt_fb_notifier_callback` 内 `queue_work_on(system_wq,…)` | 2c-3 删掉 work | 我们绕开 |
| `gConEmiPhyBase`/`gConEmiSize` | **UND（内核导出，consys 0xbf000000）** | ba2c5a5 里仅在 X86 UT 块定义（`gl_init.c:191-197`），真机为 UND → 同样走内核导出 | **一致** |
| `gConEmiPhyBaseFinal`/`gConEmiSizeFinal` | 模块内 GLOBAL `.bss`（EMI download 用） | 模块内定义（EMI=1 下强符号） | 一致 |
| `wlanDownloadEMISection` | 472 B（真实现） | `MTK_ANDROID_EMI=y` 后真实现 | 一致 |
| `kalSetSuspendFlagToEMI` | 180 B（真实现） | 同源码 | 一致 |
| 固件 / init | `soc1_0_1_1` 全套 / 原厂 init.rc 自动加载 64 位 .ko | 同套同源 | 一致 |

---

## 4. 为什么 §9.16 的"空指针写 EMI"方向被证伪

`gl_kal.c:8866-8897` 的真实逻辑：

```
8874  if (!gConEmiPhyBase) {            // 真机上 gConEmiPhyBase 由内核导出=0xbf000000，为假
8875    #if (CFG_SUPPORT_CONNINFRA == 1) // 本项目 CONNINFRA=0 → 不编译
8876      conninfra_get_phy_addr(...);
8879    #endif
8881    if (!gConEmiPhyBase) {
8883      "[EMI_Suspend] gConEmiPhyBase invalid";
8884      return WLAN_STATUS_FAILURE;      // 就算为空也会在这里提前返回，到不了 8893
8885    }
8886  }
8893  wf_ioremap_write((gConEmiPhyBase + u4Offset), suspendFlag);
```

- `gConEmiPhyBase`（无 Final）**不在模块内定义**：`gl_init.c:191-197` 只在 `#if UT_TEST_MODE && CFG_BUILD_X86_PLATFORM` 里定义，arm64 真机上是 **UND → 由 archimedes 内核 `connectivity_build_in_adapter.c` 的 consys `RESERVEDMEM_OF_DECLARE` 提供**（= `rmem->base` = `0xbf000000`）。
- 因此 `8874 if(!gConEmiPhyBase)` 为假 → 直接写 `0xbf000000 + offset`，**EMI 挂起标志被真实写入固件**，与 agui vendor 行为一致。
- 即便 CONNINFRA 有意外，`8884` 也提前 return，**不会**空指针写。

> ⚠️ §9.16"后续根因修复：需要确认 CONNINFRA，若为 0 则空指针写"这句话方向有误。正确做法见 §6。

---

## 5. 复现审计

```bash
# 从 agui 的 vendor.img 提取并审计（需要 debugfs）
python3 port/audit_agui_vendor.py --image vendor.img

# 直接对已提取的模块审计
python3 port/audit_agui_vendor.py -k wlan_drv_gen4m.ko -w wmt_drv.ko
```

对 agui 模块的期望输出（全部 PASS）：

```
gConEmiPhyBase            UND  (kernel export)               → GOOD
wlanDownloadEMISection    size=472  (real impl)              → GOOD
priv_driver_set_suspend_mode calls wlanSetSuspendMode+p2p    → GOOD (无 2c-2)
wmt_fb_notifier_callback calls queue_work_on                 → GOOD (无 2c-3)
```

在**我们自己编出**的 `wlan_drv_gen4m.ko` 上跑同一命令，若出现：
- `gConEmiPhyBase is module-defined` → 说明没吃到内核导出，需核对内核补丁；
- `suspend path MISSING` / `fb POWERDOWN work REMOVED` → 说明 workaround 还在，尚未恢复真 suspend。

---

## 6. 下一步建议（照 agui）

1. **在编出的 `.ko` 上跑 `audit_agui_vendor.py`**，确认四项不变量与 agui 一致（预期一致，因为同源码同内核）。
2. **摘掉 2c-2 / 2c-3 两个 workaround**，恢复真 suspend（agui 4G 证明不需要）。
3. 若恢复后仍卡死，才回协议层：`wlanSetSuspendMode → kalIoctl(main_thread) → FW suspend 命令 → 不回复 → 30s×4`，属于 main_thread↔FW 命令交互 / 固件状态机，**与 DMA 无关**；届时抓 `halShowDmaschInfo`（确认 EMI 修复后 group15 已归位）与 FW 对 suspend 命令的响应时序。

---

*生成于 2026-10-05，基于 `vendor.img` 实读 + `agui4541/archimedes-kernel-64` 内核源码。*
