#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recursively extract a subtree out of a raw ext4 image, preserving layout.

ext4_read.py can already `cat` a single file, but building a 30-file /lib64
tree (and the config files around it) one invocation at a time is slow and
error-prone.  This reuses the same reader and walks the subtree once.

Usage:
  ext4_extract.py <image> <in-image-dir> <out-dir> [--sizes]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ext4_read import Ext4            # noqa: E402


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    img, src, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    fs = Ext4(img)
    nfiles = nbytes = 0

    def walk(ino, rel):
        nonlocal nfiles, nbytes
        for name, dino, ftype in sorted(fs.list_dir(ino)):
            if name in ('.', '..'):
                continue
            node = fs.read_inode(dino)
            dst = os.path.join(outdir, rel, name)
            if node['is_dir']:
                os.makedirs(dst, exist_ok=True)
                os.chmod(dst, node['mode'] & 0o7777)
                walk(dino, os.path.join(rel, name))
            elif node['is_lnk']:
                tgt = fs._read_any(dino, node).decode('utf-8', 'replace')
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.lexists(dst):
                    os.remove(dst)
                os.symlink(tgt, dst)
                nfiles += 1
                print("%-56s link -> %s" % (os.path.join(rel, name), tgt))
            else:
                data = fs._read_any(dino, node)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'wb') as fh:
                    fh.write(data)
                os.chmod(dst, node['mode'] & 0o7777)
                nfiles += 1
                nbytes += len(data)
                print("%-56s %10d" % (os.path.join(rel, name), len(data)))

    try:
        inode, ino = fs.resolve(src)
    except SystemExit as exc:
        sys.exit("resolve failed: %s" % exc)
    if not inode['is_dir']:
        sys.exit("%s is not a directory in %s" % (src, img))
    os.makedirs(outdir, exist_ok=True)
    walk(ino, '')
    print("\n%d files, %d bytes -> %s" % (nfiles, nbytes, outdir))


if __name__ == '__main__':
    main()
