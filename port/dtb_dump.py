#!/usr/bin/env python3
"""Minimal standalone DTB -> DTS dumper (big-endian, no external deps)."""
import sys, struct

def u32(b, o): return struct.unpack_from(">I", b, o)[0]

def dump(dtb_path):
    d = open(dtb_path, "rb").read()
    magic = u32(d, 0)
    assert magic == 0xd00dfeed, "bad magic 0x%08x" % magic
    totalsize = u32(d, 4)
    off_struct = u32(d, 8)
    off_strings = u32(d, 12)
    off_rsv = u32(d, 16)
    version = u32(d, 20)
    size_strings = u32(d, 32)
    size_struct = u32(d, 36)

    strings = d[off_strings: off_strings + size_strings]

    def get_str(off):
        end = strings.find(b"\x00", off)
        return strings[off:end].decode("utf-8", "replace")

    out = []
    out.append("/dts-v1/;")
    out.append("")

    # memory reservations
    p = off_rsv
    while True:
        addr = u32(d, p); sz = u32(d, p + 4)
        p += 8
        if addr == 0 and sz == 0:
            break
        out.append("/memreserve/ 0x%08x 0x%08x;" % (addr, sz))

    def fmt_prop(val):
        if len(val) == 0:
            return ""
        # string
        if val[-1:] == b"\x00" and all(32 <= c < 127 or c in (9, 10, 13) for c in val[:-1]) and len(val) > 1:
            return '"%s"' % val[:-1].decode("utf-8", "replace").encode("unicode_escape").decode()
        # u32 array (big-endian cells)
        if len(val) % 4 == 0:
            cells = [u32(val, i) for i in range(0, len(val), 4)]
            return "<" + " ".join("0x%x" % c for c in cells) + ">"
        # else hex bytestring
        return "[ " + " ".join("%02x" % c for c in val) + " ]"

    pos = off_struct
    indent = 0
    def w(s): out.append(("    " * indent) + s)

    stack = []
    while pos < off_struct + size_struct:
        tok = u32(d, pos)
        if tok == 0x1:  # BEGIN_NODE
            pos += 4
            # name
            end = d.find(b"\x00", pos)
            name = d[pos:end].decode("utf-8", "replace")
            pos = end + 1
            pos = (pos + 3) & ~3
            if name == "":
                w("/ {");
            else:
                w("%s {" % name)
            indent += 1
            stack.append(name)
        elif tok == 0x2:  # END_NODE
            pos += 4
            indent -= 1
            w("};")
            stack.pop()
        elif tok == 0x3:  # PROP
            pos += 4
            plen = u32(d, pos); nameoff = u32(d, pos + 4)
            pos += 8
            val = d[pos: pos + plen]
            pos += plen
            pos = (pos + 3) & ~3
            name = get_str(nameoff)
            fv = fmt_prop(val)
            if fv == "":
                w("%s;" % name)
            else:
                w("%s = %s;" % (name, fv))
        elif tok == 0x4:  # NOP
            pos += 4
        elif tok == 0x9:  # END
            break
        else:
            raise ValueError("unknown token 0x%x at 0x%x" % (tok, pos))

    return "\n".join(out)

if __name__ == "__main__":
    src = sys.argv[1]
    txt = dump(src)
    outp = sys.argv[2] if len(sys.argv) > 2 else None
    if outp:
        open(outp, "w").write(txt)
        print("wrote", outp, len(txt), "chars")
    else:
        print(txt)
