#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk a whole ext4 image once and emit a flat inventory.

The bundled ext4_read.py `tree`/`find` commands re-resolve every path from the
root, which is O(n^2) and unusable on a 400 MB / tens-of-thousands-of-files
vendor image.  This walks the directory graph directly, caching each directory
listing by inode, and never reads regular-file payloads (sizes come straight
from the inode), so a full /vendor sweep takes a few seconds.

Output (tab separated, sorted by path):
    <size>\t<type>\t<mode>\t<path>

  type = d | f | l | c | b | ?
  mode = octal permission bits

With --diff it prints only entries whose (size,type) differ between the two
images, plus files unique to either side -- i.e. exactly the question "what did
A add/change relative to B".

Usage:
  ext4_inventory.py <image> [out.txt]
  ext4_inventory.py --diff <A.img> <B.img>
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ext4_read import Ext4            # noqa: E402

TYPES = {1: 'f', 2: 'd', 3: 'c', 4: 'b', 5: 'x', 6: 's', 7: 'l', 8: 'k'}


def inventory(img):
    fs = Ext4(img)
    out = []
    seen = set()

    def walk(ino, path):
        # cycle guard: a corrupt/hardlinked dir graph must not hang us
        if ino in seen:
            return
        seen.add(ino)
        try:
            entries = fs.list_dir(ino)
        except Exception as exc:                       # noqa: BLE001
            out.append((-1, '?', 0, path + "  <DIRREAD-FAIL %s>" % exc))
            return
        for name, dino, ftype in entries:
            if name in ('.', '..'):
                continue
            child = path + '/' + name if path != '/' else '/' + name
            try:
                node = fs.read_inode(dino)
            except Exception:                          # noqa: BLE001
                out.append((-1, '?', 0, child))
                continue
            kind = TYPES.get(ftype, '?')
            out.append((node['size'], kind, node['mode'] & 0o7777, child))
            if kind == 'd':
                walk(dino, child)

    walk(fs.root_ino, '')
    out.sort(key=lambda r: r[3])
    return out


def write(rows, path=None):
    lines = ["%d\t%s\t%04o\t%s" % r for r in rows]
    if path:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    return lines


def diff(a_rows, b_rows):
    A = {r[3]: r for r in a_rows}
    B = {r[3]: r for r in b_rows}
    only_a = sorted(set(A) - set(B))
    only_b = sorted(set(B) - set(A))
    changed = []
    for p in sorted(set(A) & set(B)):
        ra, rb = A[p], B[p]
        if ra[:2] != rb[:2]:                # size or type differ
            changed.append((p, ra, rb))
    return only_a, only_b, changed


def main():
    args = sys.argv[1:]
    if args and args[0] == '--diff':
        a_img, b_img = args[1], args[2]
        A_rows = inventory(a_img)
        B_rows = inventory(b_img)
        A = {r[3]: r for r in A_rows}
        B = {r[3]: r for r in B_rows}
        print("A = %s  (%d entries)" % (a_img, len(A)))
        print("B = %s  (%d entries)" % (b_img, len(B)))
        only_a, only_b, changed = diff(A_rows, B_rows)
        print("\n=== only in A (%d) ===" % len(only_a))
        for p in only_a:
            print("  %10d %s  %s" % (A[p][0], A[p][1], p))
        print("\n=== only in B (%d) ===" % len(only_b))
        for p in only_b:
            print("  %10d %s  %s" % (B[p][0], B[p][1], p))
        print("\n=== size/type changed (%d) ===" % len(changed))
        for p, ra, rb in changed:
            print("  %s  %s:%d -> %s:%d" % (p, ra[1], ra[0], rb[1], rb[0]))
        return

    img = args[0]
    rows = inventory(img)
    out = args[1] if len(args) > 1 else None
    lines = write(rows, out)
    print("%s: %d entries%s" % (img, len(rows),
                                (" -> " + out) if out else ""))
    if not out:
        print('\n'.join(lines))


if __name__ == '__main__':
    main()
