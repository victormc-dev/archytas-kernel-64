#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produce the Wi-Fi (Archytas) 64-bit-adapted `vendor.img`.

Recipe — mirror of what the proven 4G package (agui) does, but built on the
Wi-Fi stock vendor instead of the 4G one:

    Wi-Fi stock vendor (build 1754, 400 MiB ext4)
      + /lib64/**            29 aarch64 libs: GPU (PowerVR libusc/libsrv_um/
                             libIMGegl/libglslcompiler), EGL/GLES, gralloc +
                             hwcomposer + memtrack + graphics.mapper, ion,
                             libdpframework, PQ (libpq_*, pq@2.x HAL)
      + /lib/modules/*.ko    our aarch64 connectivity modules (drain-loop fix
                             + workaround opt-out, CI run 37412241394)
      ~ /default.prop        ro.zygote=zygote32 -> zygote64_32

The zygote change is not cosmetic: neither the GSI's /system/etc/prop.default
nor its /system/build.prop defines ro.zygote, so /vendor/default.prop is the
only writer on this device (verified with `getprop ro.zygote` + grep on both
GSIs).  Left at zygote32 the arm64 GSI would only ever start a 32-bit zygote.

No sepolicy change is needed: vendor_file_contexts + plat_file_contexts already
carry `same_process_hal_file` rules for every one of the 29 libs (the stock ROM
was built from the same MTK tree that ships a 64-bit graphics stack), so the
contexts are copied verbatim from each lib's 32-bit twin.

The heavy lifting happens on the device — see port/device_make_vendor64.sh.

Usage:
  build_wifi64_vendor.py [--base IMG] [--lib64 DIR] [--ko DIR] [--out IMG]
"""
import argparse
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ADB = os.environ.get("ADB", "D:/Software/i4Tools9/files/adb/adb.exe")
DEV = "/data/local/tmp/wifi64"

MODULES = ("wmt_drv.ko", "wmt_chrdev_wifi.ko", "wlan_drv_gen4m.ko", "bt_drv.ko")


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def adb(*args, check=True, binary=False):
    p = subprocess.run([ADB] + list(args), capture_output=True,
                       text=not binary)
    if check and p.returncode != 0:
        sys.stderr.write((p.stderr or b"").decode("utf-8", "replace")
                         if binary else (p.stderr or ""))
        raise SystemExit("adb %s failed rc=%d" % (" ".join(args), p.returncode))
    return p.stdout


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        left = limit
        while True:
            n = 1 << 20 if left is None else min(1 << 20, left)
            if n <= 0:
                break
            b = f.read(n)
            if not b:
                break
            h.update(b)
            if left is not None:
                left -= len(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.join(
        os.path.dirname(ROOT), "_archytas_rom", "vendor_rescue.img"))
    ap.add_argument("--lib64", default=os.path.join(
        ROOT, "_ci", "vendor_audit", "agui_lib64"))
    ap.add_argument("--ko", default=os.path.join(ROOT, "_ci", "37412241394"))
    ap.add_argument("--out", default=os.path.join(
        ROOT, "wifi64_pkg", "img", "vendor.img"))
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse the image already on the device")
    a = ap.parse_args()

    for p, what in ((a.base, "base image"), (a.lib64, "lib64 tree"),
                    (a.ko, "module dir")):
        if not os.path.exists(p):
            raise SystemExit("missing %s: %s" % (what, p))

    print("== 输入 ==")
    print("   base  : %s (%d bytes)" % (a.base, os.path.getsize(a.base)))
    print("   lib64 : %s" % a.lib64)
    print("   ko    : %s" % a.ko)
    print("   out   : %s" % a.out)

    print("\n== 检查设备 ==")
    devs = adb("devices").strip().splitlines()
    if len(devs) < 2:
        raise SystemExit("没有 adb 设备在线")
    print("   " + devs[1])
    adb("root", check=False)
    adb("wait-for-device")
    print("   root=%s  enforcing=%s" % (
        adb("shell", "id -u").strip(),
        adb("shell", "getenforce").strip()))

    print("\n== 推送输入 -> %s ==" % DEV)
    adb("shell", "rm -rf %s && mkdir -p %s/ko" % (DEV, DEV))
    adb("push", a.base, "%s/base.img" % DEV)
    # lib64 tree (hw/ + egl/ + top level .so), preserving layout
    adb("push", a.lib64, "%s/" % DEV)
    adb("shell", "mv -f %s/%s %s/lib64src"
        % (DEV, os.path.basename(a.lib64), DEV))
    for m in MODULES:
        src = os.path.join(a.ko, m)
        if not os.path.exists(src):
            raise SystemExit("missing module %s" % src)
        adb("push", src, "%s/ko/%s" % (DEV, m))
    adb("push", os.path.join(HERE, "device_make_vendor64.sh"),
        "%s/device_make_vendor64.sh" % DEV)
    print("   " + adb("shell", "ls -l %s %s/lib64src %s/ko" % (DEV, DEV, DEV)).strip())

    if not a.skip_build:
        print("\n== 设备端构建（loop 挂载 / 注入 / fsck）==")
        out = adb("shell", "sh %s/device_make_vendor64.sh" % DEV)
        tail = out.strip().splitlines()
        for ln in tail:
            print("   | " + ln)
        if "RESULT: OK" not in out:
            raise SystemExit("设备端构建未成功")

    print("\n== 拉取产物 ==")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    adb("pull", "%s/vendor_out.img" % DEV, a.out)
    size = os.path.getsize(a.out)
    print("   %s (%d bytes)" % (a.out, size))
    if size != 419430400:
        raise SystemExit("产物尺寸不对：%d (应为 419430400)" % size)

    print("\n== 本地校验 ==")
    sys.path.insert(0, HERE)
    from ext4_read import Ext4                      # noqa: E402
    import elf_integrity                            # noqa: E402
    fs = Ext4(a.out)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print("   [%s] %s" % ("ok" if cond else "!!", msg))
        if not cond:
            ok = False

    ino, io = fs.resolve("/lib64")
    lib64 = [n for n, d, t in fs.list_dir(io) if t == 1]
    check(len(lib64) == 20, "/lib64 顶层 .so 共 %d 个（期望 20）" % len(lib64))
    _, hw = fs.resolve("/lib64/hw")
    _, egl = fs.resolve("/lib64/egl")
    check(len([n for n, d, t in fs.list_dir(hw) if t == 1]) == 5,
          "/lib64/hw 5 个文件")
    check(len([n for n, d, t in fs.list_dir(egl) if t == 1]) == 4,
          "/lib64/egl 4 个文件（3 .so + egl.cfg）")

    for m in MODULES:
        inode, m_ino = fs.resolve("/lib/modules/" + m)
        got = hashlib.sha256(fs._read_any(m_ino, inode)).hexdigest()
        want = sha256(os.path.join(a.ko, m))
        check(got == want, "/lib/modules/%s 与我们构建的 arm64 模块一致" % m)

    inode, dp = fs.resolve("/default.prop")
    txt = fs._read_any(dp, inode).decode("utf-8", "replace")
    check("ro.zygote=zygote64_32" in txt, "default.prop 已设为 zygote64_32")
    check("ro.vndk.version=28" in txt, "ro.vndk.version=28 保持不变")

    inode, bp = fs.resolve("/build.prop")
    btxt = fs._read_any(bp, inode).decode("utf-8", "replace")
    check("1754" in btxt, "基座是 build 1754（与设备一致）")

    # Every injected lib must be aarch64 AND structurally complete.  The
    # completeness half is not optional: a truncated .so still carries a valid
    # 20-byte ELF header, so an "is it ELF64?" test alone passes while the
    # library is unusable.  That is how 19 of these 29 libs once shipped short
    # by up to 63 KB and crash-looped SurfaceFlinger -- see elf_integrity.py.
    not64, truncated = [], []
    for sub in ("", "/hw", "/egl"):
        _, i2 = fs.resolve("/lib64" + sub)
        for n in [n for n, d, t in fs.list_dir(i2) if t == 1]:
            inode, i3 = fs.resolve("/lib64%s/%s" % (sub, n))
            data = fs._read_any(i3, inode)
            if data[:4] != b"\x7fELF":
                continue        # egl.cfg and friends are text configs
            if data[4] != 2:
                not64.append(sub + "/" + n)
            good, why = elf_integrity.check(data)
            if not good:
                truncated.append("%s/%s [%s]" % (sub, n, why))
    check(not not64, "所有注入的 .so 都是 ELF64（异常: %s）" % (not64 or "无"))
    check(not truncated,
          "所有注入的 .so 结构完整、无截断（异常: %s）" % (truncated or "无"))

    print("\n== %s ==" % ("全部校验通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
