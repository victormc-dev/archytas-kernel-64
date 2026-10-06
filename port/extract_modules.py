#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull raw module files out of a raw ext4 vendor image using the read-only
ext4 reader, so they can be compared byte-for-byte / symbol-for-symbol
against the modules this port builds.

The stock ext4_read.py only emits text via `cat`, which is useless for a
54 MB .ko (it would also try to decode it as UTF-8). This walks the same
inode/extent machinery but writes bytes straight to disk.

Usage:
  extract_modules.py <vendor.img> <out-dir> [module ...]

With no module names it lists /lib/modules and extracts the four connectivity
modules if they are present.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ext4_read import Ext4            # noqa: E402

DEFAULT_MODULES = ("wmt_drv.ko", "wmt_chrdev_wifi.ko",
                   "wlan_drv_gen4m.ko", "bt_drv.ko")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img, outdir = sys.argv[1], sys.argv[2]
    names = sys.argv[3:] or list(DEFAULT_MODULES)

    fs = Ext4(img)
    os.makedirs(outdir, exist_ok=True)

    for name in names:
        path = "/lib/modules/" + name
        try:
            inode, ino = fs.resolve(path)
            data = fs._read_any(ino, inode)
        except SystemExit as exc:
            print("%-46s FAIL %s" % (name, exc))
            continue
        if not data:
            print("%-46s EMPTY" % name)
            continue
        with open(os.path.join(outdir, name), "wb") as fh:
            fh.write(data)
        print("%-46s %10d" % (name, len(data)))


if __name__ == "__main__":
    main()
