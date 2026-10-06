#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only view over an Android sparse image (magic 0xed26ff3a).

The GSI images shipped with the 64-bit packages are *sparse* (produced by
`img2simg`); a raw 1.4 GB `/system` occupies ~1.4 GB on disk but only carries a
few hundred MB of real chunks.  Rather than expanding it to a temp file just to
read a handful of small files, this wraps the source in a seekable/readable
object that resolves chunk lookups on the fly, so the existing ext4 reader can
be pointed straight at it:

    from sparse import SparseFile
    from ext4_read import Ext4
    fs = Ext4(SparseFile(open("system.img", "rb")))

Chunk types: RAW(0xCAC1) verbatim blocks, FILL(0xCAC2) one 4-byte pattern
repeated, DONT_CARE(0xCAC3) zeros, CRC32(0xCAC4) metadata we can skip.
"""
import struct

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_FILL = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
CHUNK_CRC32 = 0xCAC4

FILE_HDR_SZ = 28
CHUNK_HDR_SZ = 12


class SparseFile:
    def __init__(self, src):
        self.src = src
        src.seek(0)
        hdr = src.read(FILE_HDR_SZ)
        magic, major, minor, fhsz, chsz, blk, total_blks, total_chunks = \
            struct.unpack_from('<IHHHHIII', hdr, 0)
        if magic != SPARSE_MAGIC:
            raise ValueError("not an Android sparse image (magic=%#x)" % magic)
        self.version = (major, minor)
        self.blk = blk
        self.total_blks = total_blks
        self.total_size = blk * total_blks

        # (out_blk_start, nblks, type, data_off, fill)
        self.chunks = []
        off = fhsz
        out = 0
        fill = b'\x00\x00\x00\x00'
        for _ in range(total_chunks):
            src.seek(off)
            ctype, _res, csz, tsz = struct.unpack('<HHII', src.read(CHUNK_HDR_SZ))
            data_off = off + chsz
            if ctype == CHUNK_FILL:
                src.seek(data_off)
                fill = src.read(4)
                if len(fill) < 4:
                    fill = fill.ljust(4, b'\x00')
            self.chunks.append((out, csz, ctype, data_off, fill))
            off += tsz
            out += csz
        self._out_starts = [c[0] for c in self.chunks]
        self.pos = 0

    # ---- lookup -------------------------------------------------------
    def _chunk_for(self, blk):
        """Binary search the chunk whose output range covers `blk`."""
        lo, hi = 0, len(self.chunks)
        while lo < hi:
            mid = (lo + hi) // 2
            start, n, _t, _o, _f = self.chunks[mid]
            if blk < start:
                hi = mid
            elif blk >= start + n:
                lo = mid + 1
            else:
                return self.chunks[mid]
        return None

    # ---- file protocol ------------------------------------------------
    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.total_size + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.total_size - self.pos
        n = min(n, self.total_size - self.pos)
        if n <= 0:
            return b''
        out = bytearray()
        pos = self.pos
        end = pos + n
        while pos < end:
            blk = pos // self.blk
            in_off = pos % self.blk
            ch = self._chunk_for(blk)
            if ch is None:
                # hole beyond the last chunk -> zero fill
                take = min(self.blk - in_off, end - pos)
                out += b'\x00' * take
                pos += take
                continue
            cstart, cn, ctype, data_off, fill = ch
            blk_idx = blk - cstart
            room = (cn - blk_idx) * self.blk - in_off
            take = min(room, end - pos)
            if ctype == CHUNK_RAW:
                self.src.seek(data_off + blk_idx * self.blk + in_off)
                out += self.src.read(take)
            elif ctype == CHUNK_FILL:
                pat = fill
                start = in_off % 4
                seg = (pat * ((take + start) // 4 + 2))[start:start + take]
                out += seg
            else:                       # DONT_CARE / CRC32 payload
                out += b'\x00' * take
            pos += take
        self.pos = pos
        return bytes(out)

    def close(self):
        self.src.close()


def is_sparse(path):
    with open(path, 'rb') as f:
        return struct.unpack('<I', f.read(4))[0] == SPARSE_MAGIC


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == 'expand':
        # expand <sparse.img> <raw.img>  -- write the fully materialised image
        sf = SparseFile(open(sys.argv[2], 'rb'))
        total = sf.total_size
        done = 0
        with open(sys.argv[3], 'wb') as out:
            while done < total:
                b = sf.read(1 << 22)
                if not b:
                    break
                out.write(b)
                done += len(b)
        print("expanded %s -> %s (%d bytes)" % (sys.argv[2], sys.argv[3], done))
        raise SystemExit(0)
    sf = SparseFile(open(sys.argv[1], 'rb'))
    print("sparse v%d.%d blk=%d total_blks=%d chunks=%d"
          % (sf.version[0], sf.version[1], sf.blk, sf.total_blks, len(sf.chunks)))
    print("展开大小 = %d 字节 (%.3f GiB)" % (sf.total_size, sf.total_size / 2**30))
