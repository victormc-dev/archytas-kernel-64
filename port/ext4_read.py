#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal read-only ext4 reader for raw partition images (no external deps).

Supports: 32/64-bit block group descriptors, extent trees (depth 0/1),
linear directory entries, and inline data for tiny files. Good enough to
inventory /vendor/lib* and read small XML/rc files from a vendor.bin dump.

Usage:
  ext4_read.py <image> list  <dir>
  ext4_read.py <image> cat   <file>
  ext4_read.py <image> find  <dirname> [maxdepth]
  ext4_read.py <image> tree  <dir> [maxdepth]
"""
import sys, struct

class Ext4:
    def __init__(self, path):
        # `path` may be a filename or any seekable/readable object.  The latter
        # lets sparse-image wrappers (see sparse.py) be read without first
        # expanding a 1.4 GB system image to disk.
        self.path = path
        self.f = path if hasattr(path, 'read') else open(path, 'rb')
        self.f.seek(1024)
        sb = self.f.read(1024)
        if sb[0x38:0x3a] != b'\x53\xef':
            raise SystemExit("not an ext4 superblock (magic=%r)" % sb[0x38:0x3a])
        def u(off, n):
            return struct.unpack_from('<' + ('I' if n == 4 else 'H'), sb, off)[0]
        self.block_size = 1024 << u(0x18, 4)
        self.blocks_per_group = u(0x20, 4)
        self.inodes_per_group = u(0x28, 4)
        self.inode_size = u(0x58, 2) or 128
        self.first_data_block = u(0x14, 4)
        self.first_ino = u(0x54, 4) or 11
        self.incompat = u(0x60, 4)
        self.has_64bit = bool(self.incompat & 0x80)
        sb_block = 1024 // self.block_size
        gdt_block = sb_block + 1
        self.gdt_off = gdt_block * self.block_size
        self.desc_size = 64 if self.has_64bit else 32
        # root dir inode
        self.root_ino = 2

    def read_block(self, n):
        self.f.seek(n * self.block_size)
        return self.f.read(self.block_size)

    def read_blocks(self, lst):
        out = bytearray()
        for b in lst:
            out += self.read_block(b)
        return bytes(out)

    def inode_table_block(self, ino):
        bg = (ino - 1) // self.inodes_per_group
        idx = (ino - 1) % self.inodes_per_group
        # read bg descriptor
        self.f.seek(self.gdt_off + bg * self.desc_size)
        d = self.f.read(self.desc_size)
        it_lo = struct.unpack_from('<I', d, 0x08)[0]
        it_hi = struct.unpack_from('<I', d, 0x28)[0] if self.has_64bit else 0
        it = it_lo | (it_hi << 32)
        per = self.block_size // self.inode_size
        tbl_blk = it + idx // per
        off_in_blk = (idx % per) * self.inode_size
        self.f.seek(tbl_blk * self.block_size + off_in_blk)
        return self.f.read(self.inode_size)

    def read_inode(self, ino):
        raw = self.inode_table_block(ino)
        mode = struct.unpack_from('<H', raw, 0)[0]
        size_lo = struct.unpack_from('<I', raw, 4)[0]
        size_hi = struct.unpack_from('<I', raw, 108)[0]
        size = size_lo | (size_hi << 32)
        flags = struct.unpack_from('<I', raw, 0x20)[0]
        i_block = raw[40:40+60]
        return {'ino': ino, 'mode': mode, 'size': size, 'flags': flags,
                'i_block': i_block,
                'is_dir': (mode & 0xF000) == 0x4000,
                'is_lnk': (mode & 0xF000) == 0xA000,
                'is_reg': (mode & 0xF000) == 0x8000}

    def _extent_blocks(self, i_block, depth):
        """Yield (start_block, block_count) leaves of the extent tree."""
        magic, entries, _max, d = struct.unpack_from('<HHHH', i_block, 0)
        if magic != 0xF30A:
            # old-style block pointers (pre-extent). Rare on ext4; bail.
            raise SystemExit("non-extent inode not supported")
        data = i_block[12:]
        for i in range(entries):
            rec = data[i*12:(i+1)*12]
            if depth == 0:
                ee_block, ee_len, ee_hi, ee_lo = struct.unpack('<IHHI', rec)
                start = ee_lo | (ee_hi << 32)
                length = ee_len & 0x7FFF
                yield (start, length)
            else:
                ei_block, ei_leaf_lo, ei_leaf_hi, _ = struct.unpack('<IIHI', rec)
                leaf = ei_leaf_lo | (ei_leaf_hi << 32)
                sub = self.read_block(leaf)
                yield from self._extent_blocks(sub, depth - 1)

    def file_blocks(self, ino):
        """Return ordered list of data block numbers for inode (extent leaves)."""
        inode = self.read_inode(ino)
        # inline data?
        if inode['is_reg'] and (inode['flags'] & 0x10000000) and inode['size'] > 0:
            # data stored in i_block after 4-byte magic header
            return [('inline', inode['i_block'])]
        blocks = []
        d0 = struct.unpack_from('<HHHH', inode['i_block'], 0)[3]  # eh_depth
        # re-read depth from header properly
        magic, entries, _max, depth = struct.unpack_from('<HHHH', inode['i_block'], 0)
        for (start, length) in self._extent_blocks(inode['i_block'], depth):
            blocks.extend(range(start, start + length))
        return blocks

    def read_file(self, ino):
        inode = self.read_inode(ino)
        if inode['size'] == 0:
            return b''
        if isinstance(self.file_blocks(ino)[0], tuple) if (inode['is_reg'] and (inode['flags'] & 0x10000000)) else False:
            # inline
            raw = self.file_blocks(ino)
            # raw is [('inline', i_block_bytes)]
            ib = raw[0][1]
            if ib[:4] == b'\x00\xdf\x00\xad':
                return ib[4:4 + inode['size']]
            return ib[:inode['size']]
        blocks = self.file_blocks(ino)
        data = self.read_blocks(blocks)
        return data[:inode['size']]

    def list_dir(self, ino):
        """Return list of (name, inode, file_type)."""
        data = self.read_file(ino) if not self.read_inode(ino)['is_dir'] else None
        # read_file handles dir inodes too (they're regular files of dir entries)
        inode = self.read_inode(ino)
        data = self._read_any(ino, inode)
        out = []
        off = 0
        while off + 8 <= len(data):
            dinode, rec_len, name_len, ftype = struct.unpack_from('<IHBB', data, off)
            if rec_len == 0:
                break
            name = data[off+8:off+8+name_len].decode('utf-8', 'replace')
            if dinode != 0:
                out.append((name, dinode, ftype))
            off += rec_len
        return out

    def _read_any(self, ino, inode):
        if inode['size'] == 0:
            return b''
        if inode['is_reg'] and (inode['flags'] & 0x10000000) and inode['size'] > 0:
            ib = inode['i_block']
            if ib[:4] == b'\x00\xdf\x00\xad':
                return ib[4:4 + inode['size']]
            return ib[:inode['size']]
        blocks = []
        magic, entries, _max, depth = struct.unpack_from('<HHHH', inode['i_block'], 0)
        for (start, length) in self._extent_blocks(inode['i_block'], depth):
            blocks.extend(range(start, start + length))
        return self.read_blocks(blocks)[:inode['size']]

    def resolve(self, path):
        """Resolve absolute path to inode dict."""
        parts = [p for p in path.strip('/').split('/') if p]
        cur = self.read_inode(self.root_ino)
        cur_ino = self.root_ino
        for p in parts:
            if not cur['is_dir']:
                raise SystemExit("%s is not a directory" % path)
            entries = self.list_dir(cur_ino)
            hit = [e for e in entries if e[0] == p]
            if not hit:
                raise SystemExit("not found: %s (in %s)" % (p, path))
            name, ino, ftype = hit[0]
            cur = self.read_inode(ino)
            cur_ino = ino
            if cur['is_lnk']:
                target = self._read_any(ino, cur).decode('utf-8', 'replace')
                if not target.startswith('/'):
                    # relative symlink: rebuild path
                    base = '/' + '/'.join(parts[:parts.index(p)])
                    target = (base + '/' + target).replace('//', '/')
                return self.resolve(target)
        return cur, cur_ino


def cmd_list(img, path):
    fs = Ext4(img)
    inode, ino = fs.resolve(path)
    if not inode['is_dir']:
        print("NOT A DIR:", path)
        return
    for name, di, ftype in sorted(fs.list_dir(ino)):
        tag = {2: 'd', 1: 'f', 7: 'l', 3: 'c', 4: 'b'}.get(ftype, '?')
        print("%s %s" % (tag, name))


def cmd_cat(img, path):
    fs = Ext4(img)
    inode, ino = fs.resolve(path)
    sys.stdout.buffer.write(fs._read_any(ino, inode))


def cmd_tree(img, path, maxdepth=3):
    fs = Ext4(img)
    def walk(p, d):
        inode, ino = fs.resolve(p)
        if not inode['is_dir']:
            return
        for name, di, ftype in sorted(fs.list_dir(ino)):
            if name in ('.', '..'):
                continue
            print("  " * d + ("%s" % name) + ("/" if ftype == 2 else ""))
            if ftype == 2 and d < maxdepth:
                walk(p.rstrip('/') + '/' + name, d + 1)
    walk(path, 0)


def cmd_find(img, dirname, maxdepth=4):
    fs = Ext4(img)
    res = []
    def walk(p, d):
        inode, ino = fs.resolve(p)
        if not inode['is_dir']:
            return
        for name, di, ftype in sorted(fs.list_dir(ino)):
            if name in ('.', '..'):
                continue
            if name == dirname:
                res.append(p.rstrip('/') + '/' + name)
            if ftype == 2 and d < maxdepth:
                walk(p.rstrip('/') + '/' + name, d + 1)
    walk('/', 0)
    print('\n'.join(res))


if __name__ == '__main__':
    img = sys.argv[1]
    act = sys.argv[2]
    if act == 'list':
        cmd_list(img, sys.argv[3])
    elif act == 'cat':
        cmd_cat(img, sys.argv[3])
    elif act == 'tree':
        cmd_tree(img, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 2)
    elif act == 'find':
        cmd_find(img, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    else:
        print("unknown action", act)
