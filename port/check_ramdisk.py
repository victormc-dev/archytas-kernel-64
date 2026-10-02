#!/usr/bin/env python3
# 标准 Android boot 头解析 + ramdisk 抽取/解压/列 fstab（精确 page 对齐）
import struct, zlib, os, subprocess

P = "/Users/weitianrui/Downloads/Archytas_64"
ARCH_TPL = P + "/archimedes-kernel-64-main/tools/archimedes-boot-template-32MiB.img"
WIFI_BOOT = P + "/柴提全原无数据WiFi.9.1638/WiFi.9.1638原版/boot.bin"

def parse_boot(boot_path):
    d = open(boot_path, "rb").read()
    PAGE = struct.unpack("<I", d[0x24:0x28])[0] or 2048
    ks  = struct.unpack("<I", d[8:12])[0]
    rs  = struct.unpack("<I", d[16:20])[0]
    ss  = struct.unpack("<I", d[20:24])[0]
    kpages = (ks + PAGE - 1) // PAGE
    rpages = (rs + PAGE - 1) // PAGE
    r_off = PAGE + kpages * PAGE
    r_end = r_off + rs if rs else None
    raw = d[r_off:r_end] if r_end else d[r_off:]
    raw = raw.rstrip(b"\x00")
    # 判断压缩
    out = None
    comp = "?"
    if raw[:2] == b"\x1f\x8b":
        try:
            out = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
        except Exception:
            do = zlib.decompressobj(16 + zlib.MAX_WBITS)
            out = do.decompress(raw) + do.flush()
        comp = "gzip"
    elif raw[:4] == b"\x04\x22\x4d\x18":
        comp = "lz4"
    else:
        out = raw
        comp = "raw-cpio"
    return dict(path=boot_path, PAGE=PAGE, ks=ks, rs=rs, ss=ss,
                r_off=r_off, r_len=len(raw), comp=comp, data=out)

def cpio_names(data):
    names = []; i = 0; n = len(data)
    while i + 110 <= n:
        if data[i:i+6] not in (b"070701", b"070702"):
            break
        hdr = data[i:i+110]
        ns = int(hdr[94:102], 16); fs = int(hdr[102:110], 16)
        name = data[i+110:i+110+ns-1].decode("ascii", "replace")
        if name == "TRAILER!!!":
            break
        names.append(name)
        tot = 110 + ns; p1 = (4 - tot % 4) % 4
        ds = i + tot + p1; de = ds + fs; p2 = (4 - fs % 4) % 4
        i = de + p2
    return names

def dump_fstab(boot_path, label, ramdisk_bytes):
    if ramdisk_bytes is None:
        return
    tmp = f"/tmp/rd_{label}.cpio"
    open(tmp, "wb").write(ramdisk_bytes)
    outdir = f"/tmp/rd_{label}"
    subprocess.run(f"rm -rf {outdir} && mkdir -p {outdir} && cd {outdir} && cpio -i -F {tmp} >/dev/null 2>&1",
                   shell=True)
    res = subprocess.run(f"find {outdir} -iname '*fstab*' -o -name 'init' -o -iname '*default.prop*'",
                         shell=True, capture_output=True, text=True)
    files = [l for l in res.stdout.splitlines() if l.strip()]
    print(f"\n--- [{label}] fstab/init/prop 文件 ---")
    for f in files[:30]:
        print(f"  {f}")
        try:
            c = open(f).read()
            print("    >>> 内容摘要:")
            for line in c.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    print("      " + s)
        except Exception as e:
            print("    (读失败)", e)

for label, path in [("ARCH", ARCH_TPL), ("WIFI", WIFI_BOOT)]:
    b = parse_boot(path)
    print(f"===== {label} : {os.path.basename(path)} =====")
    print(f"  PAGE={b['PAGE']} kernel_size={b['ks']}({b['ks']/1024:.1f}KiB) "
          f"ramdisk_size={b['rs']} second={b['ss']}")
    print(f"  ramdisk 偏移={b['r_off']}(0x{b['r_off']:x}) 实际去尾零长度={b['r_len']} 压缩={b['comp']}")
    if b['data'] is not None and b['comp'] != 'lz4':
        names = cpio_names(b['data'])
        print(f"  cpio 条目数={len(names)}")
        print(f"  根目录前20: {names[:20]}")
        dump_fstab(path, label, b['data'])
    else:
        print("  (lz4/未解压，跳过)")
    print()
