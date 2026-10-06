#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`du` for a raw ext4 image -- pure Python, no external tools.

We need to know *what* is eating the space inside a GSI before deciding what to
cut, and on this box there is no e2fsprogs and no WSL (blocked by policy).  The
fine-grained answer comes from mounting the image on the device, but a local
sweep is instant and keeps working when no device is attached.

Size is **allocated blocks**, not `st_size`: an extent that is a hole or is
uninitialized occupies nothing, and reporting `st_size` would blame a sparse
file for space it never took.  Inline (in-inode) data and fast symlinks also
occupy no separate blocks.

Usage:
  ext4_du.py <image> [dir] [depth]
      dir    directory to summarise (default: /)
      depth  levels to expand, 0 = totals only (default: 1)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ext4_read import Ext4  # noqa: E402

INLINE = 0x10000000
FTYPE_REG, FTYPE_DIR, FTYPE_LNK = 1, 2, 7


def alloc_bytes(fs, inode):
    """Disk bytes the inode actually occupies."""
    if inode['is_reg'] and (inode['flags'] & INLINE):
        return 0                     # data lives in the inode itself
    if inode['is_lnk'] and inode['size'] <= 60:
        return 0                     # fast symlink
    if inode['is_lnk']:
        pass                         # slow symlink: real blocks, counted below
    total = 0
    try:
        for _lblk, _start, length, initialized in fs._extent_blocks(inode['i_block']):
            if initialized:
                total += length * fs.block_size
    except SystemExit:
        return inode['size']         # not extent-mapped; fall back to size
    return total


def walk(fs, ino, seen_inodes, hardlinks):
    """Return allocated bytes under `ino` (inclusive of the inode's own blocks)."""
    inode = fs.read_inode(ino)
    if inode['is_lnk']:
        return alloc_bytes(fs, inode)
    if not inode['is_dir']:
        return alloc_bytes(fs, inode)

    nlink = inode.get('nlink', 1)
    if ino in seen_inodes:
        return 0
    seen_inodes.add(ino)

    total = alloc_bytes(fs, inode)
    for name, ci, ftype in fs.list_dir(ino):
        if name in ('.', '..'):
            continue
        total += walk(fs, ci, seen_inodes, hardlinks)
    return total


def du_tree(fs, ino, label, depth, maxdepth, seen_inodes):
    entries = []
    for name, ci, ftype in fs.list_dir(ino):
        if name in ('.', '..'):
            continue
        ci_inode = fs.read_inode(ci)
        size = walk(fs, ci, seen_inodes, None)
        entries.append((size, name, ci, ci_inode))
    entries.sort(reverse=True)
    for size, name, ci, ci_inode in entries:
        kind = 'd' if ci_inode['is_dir'] else ('l' if ci_inode['is_lnk'] else 'f')
        print("%10.1f MiB  %s %s%s" % (size / 1048576.0, kind,
                                       label.rstrip('/') + '/', name))
        if ci_inode['is_dir'] and depth < maxdepth:
            du_tree(fs, ci, label.rstrip('/') + '/' + name, depth + 1,
                    maxdepth, seen_inodes)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    img = sys.argv[1]
    where = sys.argv[2] if len(sys.argv) > 2 else '/'
    maxdepth = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    fs = Ext4(img)
    inode, ino = fs.resolve(where)
    if not inode['is_dir']:
        print("%s is not a directory" % where)
        return

    import struct as _s
    fs.f.seek(1024)
    sb = fs.f.read(1024)
    total_blocks = _s.unpack_from('<I', sb, 0x04)[0] | (_s.unpack_from('<I', sb, 0x150)[0] << 32)
    free_blocks = _s.unpack_from('<I', sb, 0x0c)[0] | (_s.unpack_from('<I', sb, 0x158)[0] << 32)
    print("image      : %s" % img)
    print("block size : %d" % fs.block_size)
    print("fs size    : %.1f MiB (%d blocks)" % (total_blocks * fs.block_size / 1048576.0,
                                                 total_blocks))
    print("free       : %.1f MiB (%d blocks)" % (free_blocks * fs.block_size / 1048576.0,
                                                 free_blocks))
    print("used       : %.1f MiB" % ((total_blocks - free_blocks) * fs.block_size / 1048576.0))
    print("dir        : %s" % where)
    print()

    du_tree(fs, ino, where, 1, maxdepth, set())
    print()
    print("total under %s: %.1f MiB" % (where,
                                        walk(fs, ino, set(), None) / 1048576.0))


if __name__ == '__main__':
    main()
