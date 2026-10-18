#!/usr/bin/env python3
"""cpio newc 打包器(Android ramdisk):ino 截断 32 位,uid/gid=0,字段严格 8 字符"""
import os, sys

ZEROS = '00000000' * 4  # devmajor devminor rdevmajor rdevminor

def write_cpio(root):
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in sorted(dirnames):
            entries.append(('dir', os.path.join(dirpath, d)))
        for f in sorted(filenames):
            entries.append(('file', os.path.join(dirpath, f)))
    entries.sort(key=lambda e: (os.path.relpath(e[1], root).count(os.sep),
                                (0 if e[0] == 'dir' else 1),
                                os.path.relpath(e[1], root)))
    buf = bytearray()
    for typ, path in entries:
        st = os.lstat(path)
        name = os.path.relpath(path, root)
        if typ == 'dir':
            name += '/'
        name_b = name.encode()
        if typ == 'dir':
            mode = 0o40755; size = 0; data = b''
        else:
            mode = 0o100755 if (st.st_mode & 0o111) else 0o100644
            size = st.st_size
            data = open(path, 'rb').read()
        hdr = ('070701'
               f'{(st.st_ino & 0xffffffff):08x}'
               f'{mode:08x}'
               f'00000000'          # uid=root
               f'00000000'          # gid=root
               f'{1 if typ == "file" else 2:08x}'  # nlink
               f'{int(st.st_mtime) & 0xffffffff:08x}'
               f'{size:08x}'
               f'{ZEROS}'
               f'{len(name_b) + 1:08x}'  # namesize 含 null
               f'00000000').encode()     # check
        assert len(hdr) == 110, f"hdr len {len(hdr)} for {name}"
        buf += hdr + name_b + b'\x00'
        buf += b'\x00' * ((-len(buf)) % 4)
        if data:
            buf += data
            buf += b'\x00' * ((-len(data)) % 4)
    t = b'070701' + b'0' * 88 + b'0000000b' + b'00000000'  # 11 字段 + namesize(11) + check
    t = t + b'\x00' * (110 - len(t)) + b'TRAILER!!!'
    buf += t
    buf += b'\x00' * ((-len(t)) % 4)
    return bytes(buf)

if __name__ == '__main__':
    root, out = sys.argv[1], sys.argv[2]
    cpio = write_cpio(root)
    with open(out, 'wb') as f:
        f.write(cpio)
    print(f"{out}: {len(cpio)} bytes")
