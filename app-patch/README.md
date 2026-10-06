# app-patch —— 在无 JDK 环境下给第三方 APK「打补丁 + 重签名」

## 起因：RayNeo iO 在 Android 10 GSI 上秒崩

设备（Archytas / MT6761）刷上 **LineageOS 17.1（Android 10）** 后，雷鸟 AI 眼镜的配套应用
`com.rayneo.venus.pub`（v1.0.5，targetSdk 36）**点开就闪退**，连启动图都看不到。

### 症状（实测）

```
Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x18
in tid 4611 (1.raster)  >>>  com.rayneo.venus.pub
Cause: null pointer dereference
  x1 = 0
  #00..#11  /data/app/.../lib/arm64/libflutter.so
```

配合 logcat 里唯一一条真正的错误：

```
E/flutter: [ERROR:flutter/shell/platform/android/android_context_gl_impeller.cc(151)]
           Could not choose offscreen config.
```

## 根因：Flutter 的 GL-Impeller 与 PowerVR 的 EGL 驱动不兼容

完整因果链（每一条都有实机判据）：

| 环节 | 事实 | 判据 |
|---|---|---|
| GPU | **PowerVR (Imagination)** | SF 进程 maps 里有 `/vendor/lib64/libIMGegl.so` |
| GLES | 硬件加速**正常** | `dumpsys gpu` → `glLoadingCount=14 glLoadingFailureCount=0` |
| Vulkan | **不可用** | `dumpsys gpu` → `vkLoadingFailureCount=1 vulkanVersion=0`；无 `/vendor/etc/vulkan/icd.d/` |
| Flutter 后端选择 | Impeller **只在 API ≥ 29 启用** | 设备是 Android 10（API 29）→ 启用 |
| Impeller 回退链 | Vulkan 失败 → **GLES-Impeller** | 报错文件名 `android_context_gl_impeller.cc` |
| 失败点 | GLES-Impeller 需要 `EGL_PBUFFER_BIT` + RGBA8888 + ES2/3 + depth0/stencil0 的 **offscreen config**，**PowerVR 的 EGL 驱动不提供** | `eglChooseConfig` 返回 0 → 报上面那行 |
| 崩溃 | libflutter 没判空 → `raster` 线程空指针 | tombstone `Cause: null pointer dereference`, `x1=0`, fault addr `0x18` |

**为什么 Android 9 上能开**：Flutter 的 Impeller 要求 **API ≥ 29**。
Android 9 = API 28 → 走 **Skia GL** 后端，它用 *window surface + FBO*，
**不需要 pbuffer config**。所以同一个 APK 在 Android 9 正常、在 Android 10 必崩。

> 顺带排除的两个误判：
> - `/vendor/lib64/egl/egl.cfg` 里写的是 `mali`（MTK 模板残留），但目录里只有
>   `libEGL_mtk.so` —— EGL loader 对空 `ro.hardware.egl` 会回退扫描目录，
>   **实际加载的是正确的硬件驱动**（见上面 maps 判据），不是问题所在。
> - `/system/lib64/egl/` 不存在、没有软件 EGL 兜底，所以也不是"退化到软件渲染"。

## 解法：注入两个 meta-data 强制 Flutter 回 Skia

```xml
<meta-data android:name="io.flutter.embedding.android.ImpellerBackend" android:value="none"/>
<meta-data android:name="io.flutter.embedding.android.EnableImpeller" android:value="false"/>
```

两个都写是为了覆盖不同 Flutter 版本（3.29+ 认前者，`EnableImpeller=false` 是向后兼容的旧键）。

**试过但不行的路子**（记下来省事）：

| 尝试 | 结果 |
|---|---|
| `am start … --ez disable-impeller true` | ❌ 无效 |
| `am start … --ez enable-impeller false` | ❌ 无效 |
| `am start … --ez enable-software-rendering true` | ❌ 无效 |

原因：logcat 里有一行 `I/FlutterRuntime: ensureStarted engine cached id=main_background_engine`
—— 这个 app **自己缓存并复用 FlutterEngine**，绕过了 `FlutterActivity` 的
`FlutterShellArgs.fromIntent()`，所以 Intent 参数完全不起作用。
只有 **AndroidManifest 的 meta-data**（由 PackageManager 在安装时解析）才有效。

## 工具链：本机没有 java，怎么办

本机**没有 java / javac / keytool / apksigner / apktool**。所以：

| 需求 | 替代方案 |
|---|---|
| 改二进制 `AndroidManifest.xml` (AXML) | 自己写 AXML 解析/重建（`axml.py` + `inject_impeller.py`） |
| 重新打包 zip | 手写 zip 重写器（raw 复制 + 对齐，见 `repack_v1sign.py`） |
| APK 签名 | **v1（JAR signing）纯 Python 实现** + `openssl` 生成 PKCS#7 |

**为什么 v1 就够**：Android 10（API 29）接受 v1-only；
「必须 v2+」是从 **API 30 + targetSdk ≥ 30** 才开始的。

## 三个脚本

```bash
PY=C:/Users/ASUS/.workbuddy/binaries/python/versions/3.13.12/python.exe

# 1) 查看/定位 AXML 结构（确认 meta-data、字符串池、application 节点）
"$PY" axml.py AndroidManifest.xml

# 2) 注入 Flutter Impeller 开关（自动重建 UTF-16 字符串池 + 自检）
"$PY" inject_impeller.py AndroidManifest.xml AndroidManifest_patched.xml

# 3) 重新打包 + v1 签名（含对齐校验、zip 完整性自检）
"$PY" repack_v1sign.py base_orig.apk AndroidManifest_patched.xml base_patched.apk
```

本目录已生成：**`rayneo-venus-1.0.5-impeller-off.apk`**（406 MB，未入库）。

### 安装

```bash
adb uninstall com.rayneo.venus.pub          # 签名变了，必须先卸载
adb install -r rayneo-venus-1.0.5-impeller-off.apk
```

## 实测结果

```
am start -W → Status: ok
mResumedActivity: com.rayneo.venus.pub/com.rayneo.venus.MainActivity   ← 是真的在前台
进程存活（20 s 后仍为同一 pid），无新 tombstone
截图确认：RayNeo iO 登录页完整渲染（Logo / 标题 / 「使用手机号注册&登录」/ 隐私勾选）
```

## 三个技术坑（都踩过，值得记）

### 1. AXML 没有全局偏移索引 ⇒ 插入元素只需改 size

元素是线性的 `START_ELEMENT`/`END_ELEMENT` chunk 流，**不存在指向别处的指针**，
所以插入字节后**不用修正任何偏移**，只要同步文件头和池 chunk 的 `size`。

另外：**属性识别靠 `RES_XML_RESOURCE_MAP`**（索引 == 字符串池索引）。
所以 `android:name` / `android:value` 必须**复用池里已有的字符串索引**（本机是 3 和 22，
且都 `< resmap 长度 48`）；新增的字符串只能**追加到池尾**，这样 resmap 前 N 项映射不变。

### 2. ★ `CERT.SF` 里条目级的 `SHA-256-Digest` 不是文件内容的摘要

是 **MANIFEST.MF 中「该条目那一段字节」的摘要**。第一版我只写了
`SHA-256-Digest-Manifest`（`apksigner` 的 v1 就是这么省的），结果安装报：

```
INSTALL_PARSE_FAILED_NO_CERTIFICATES:
  Package .../base.apk has no certificates at entry AndroidManifest.xml
```

补上**每个条目**的段摘要（`jarsigner` 的做法）后立即通过。

### 3. 重打包的对齐

- `.so` / `.dex` 在本 APK 里全是 **DEFLATE** ⇒ **无页对齐要求**；
- `resources.arsc` 是 **STORED** ⇒ **data offset 必须 4 字节对齐**（AssetManager 要 mmap）；
- `AndroidManifest.xml` 在 zip 中部，改动它会平移后面所有条目
  ⇒ 脚本用 **padding extra**（id `0xd935`）把总位移 `Δ` 调成 **4 的倍数**，
  后续条目的对齐关系就整体保持不变（实测 `Δ=104`）。

原 APK 只有 **v2/v3 签名**（没有 `META-INF/*.SF`），重打包时**丢弃 zip 里的
APK Signing Block**（它在中央目录之前，本脚本按其结构天然不复制）。

## 可复现的后续

如果想验证"到底是不是 Impeller 的锅"，可以拿任意一个 **API 29+ 上的 Flutter app**
在这台设备上跑：只要它崩在 `raster` 线程且日志有 `Could not choose offscreen config`，
就是同一个问题，用同一套脚本即可。

同类设备（PowerVR/老 EGL 驱动 + Android 10+）**都会中招**，与是否精简 GSI 无关。
