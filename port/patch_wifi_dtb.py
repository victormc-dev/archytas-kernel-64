#!/usr/bin/env python3
"""Patch the Archytas (MT6761 Wi-Fi board) DTB so the Gen4M WLAN driver can
bind its reserved-memory DMA pool.

Why this is needed
------------------
The Gen4M WLAN core (zainarbani/vendor-mediatek-...-wlan-core-gen4m, patched by
agui4541) calls of_reserved_mem_device_init(&pdev->dev) from axiDmaSetup(). On
this device that call returns -ENODEV (-19) because the device-tree node

    wifi@18000000 { compatible = "mediatek,wifi"; ... }

has no "memory-region" property at all, so the phandle lookup fails.

Two independent problems hide behind that single -19:

  1. the wifi node lacks "memory-region", so the phandle lookup inside
     __of_reserved_mem_device_init() returns NULL -> -ENODEV;

  2. even with a phandle, the target node must actually become a coherent DMA
     pool.  The Wi-Fi ROM DTB declares it as a *plain* reserved region:

         compatible = "mediatek,wifi-reserve-memory";
         no-map;
         size = <0x0 0x300000>;
         alignment = <0x0 0x1000000>;
         alloc-ranges = <0x0 0x40000000 0x0 0x80000000>;

     Only "mediatek,wifi-reserve-memory" matches the RESERVEDMEM_OF_DECLARE in
     drivers/misc/mediatek/connectivity/common/connectivity_build_in_adapter.c,
     whose callback merely records base/size and leaves rmem->ops == NULL, so
     of_reserved_mem_device_init() would then fail with -EINVAL.

     A usable pool needs "shared-dma-pool" in the compatible list.  There are
     two RESERVEDMEM_OF_DECLARE entries for that string, and the *no-map*
     property is what picks between them:

         rmem_cma_setup()   (drivers/base/dma-contiguous.c, linked first)
             returns -EINVAL when "no-map" is present  -> skipped
         rmem_dma_setup()   (drivers/base/dma-coherent.c, linked next)
             installs rmem->ops = &rmem_dma_ops       -> wins

     So "no-map" must be *kept*.  The resulting pool is a memremap()'d
     coherent pool, which is what rmem_dma_device_init() needs.

The exact node to aim for is the one the *newer 4G firmware DTB*
(4g_stock.dtb) ships for the same SoC:

    wifi-reserve-memory {
        phandle = <0xa8>;
        reg = <0x0 0x7f000000 0x0 0x300000>;
        compatible = "shared-dma-pool", "mediatek,wifi-reserve-memory";
        no-map;
        size = <0x0 0x300000>;
        alignment = <0x0 0x1000000>;
    };

i.e. add "shared-dma-pool", add a fixed "reg", drop "alloc-ranges", add a
phandle, and add "memory-region" on the wifi node.  Everything else in the
Wi-Fi board DTB is left byte-for-byte alone -- notably the charger current
limits, which do differ between the 4G and Wi-Fi boards.

Why 0x7f000000 is safe on this unit
-----------------------------------
Bootloader (ATF/mblock) reserved windows observed on the live device:

    mblock-6-SSPM-reserved   0x7feb0000 .. 0x7ffb0000
    mblock-7-framebuffer     0x7f980000 .. 0x7feb0000
    mblock-3-log_store       0x7ffbf000 .. 0x7ffff000
    mblock-9-SPM-reserved    0x77ff0000 .. 0x78000000

0x7f000000 .. 0x7f300000 (3 MiB) collides with none of them.  DRAM runs to
0xc0000000, so the window is inside RAM.

The script is idempotent: running it on an already-patched DTB is a no-op.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

WIFI_NODE = "wifi@18000000"
WIFI_RMEM_NODE = "wifi-reserve-memory"
CONSYS_RMEM_NODE = "consys-reserve-memory"
RESERVED_MEMORY_NODE = "reserved-memory"

WIFI_RMEM_BASE = 0x7F000000
WIFI_RMEM_SIZE = 0x300000
WIFI_RMEM_COMPAT = b"shared-dma-pool\x00mediatek,wifi-reserve-memory\x00"

# CONSYS EMI window -- where the bootloader/LK and WMT STP firmware expect
# the 4 MB shared-memory mailbox.  Observed live from WMT boot log:
# "Clearing Connsys EMI physical(0xbf000000) 4194304 bytes".
# The DT's original consys-reserve-memory uses alloc-ranges (dynamic), so
# the kernel's reserved-memory allocator picks some address inside
# 0x40000000..0xC0000000 -- which DOES NOT match the hard-coded window the
# WMT driver programs into the CONSYS chip.  Result: STP share-memory
# structs end up at two different physical addresses -> gRxCount = -1
# garbage -> HOST_AWAKE/ FW_START handshake timeouts -> wlan never appears.
# Fix: pin it to the same 0xbf000000 window the bootloader and WMT use.
# The size change is ZERO: swap alloc-ranges (16 bytes of struct value) for
# reg (16 bytes of struct value); both property names are already in the
# original strings block, so totalsize stays 84094.
CONSYS_RMEM_BASE = 0xBF000000
CONSYS_RMEM_SIZE = 0x400000
CONSYS_RMEM_COMPAT = b"mediatek,consys-reserve-memory\x00"


def u32be(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def p32(v: int) -> bytes:
    return struct.pack(">I", v)


def pad4(n: int) -> int:
    return (n + 3) & ~3


def pad8(n: int) -> int:
    return (n + 7) & ~7


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name: str) -> None:
        self.name = name
        self.props: list[tuple[str, bytes]] = []
        self.children: list["Node"] = []

    def get(self, name: str):
        for n, v in self.props:
            if n == name:
                return v
        return None

    def set(self, name: str, value: bytes) -> None:
        for i, (n, _) in enumerate(self.props):
            if n == name:
                self.props[i] = (name, value)
                return
        self.props.append((name, value))

    def drop(self, *names: str) -> None:
        drop = set(names)
        self.props = [(n, v) for (n, v) in self.props if n not in drop]

    def child(self, name: str):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Fdt:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        if u32be(blob, 0) != FDT_MAGIC:
            raise ValueError("not a DTB (bad magic 0x%08x)" % u32be(blob, 0))
        self.totalsize = u32be(blob, 4)
        self.off_struct = u32be(blob, 8)
        self.off_strings = u32be(blob, 12)
        self.off_rsvmap = u32be(blob, 16)
        self.version = u32be(blob, 20)
        self.last_comp_version = u32be(blob, 24)
        self.boot_cpuid_phys = u32be(blob, 28)
        self.size_strings = u32be(blob, 32)
        self.size_struct = u32be(blob, 36)
        if self.totalsize != len(blob):
            raise ValueError(
                "totalsize %d != file length %d" % (self.totalsize, len(blob))
            )
        self.strings = blob[self.off_strings:self.off_strings + self.size_strings]
        self.rsvmap = blob[self.off_rsvmap:self.off_struct]
        # Property-name -> offset inside the original strings block. Kept so
        # tobytes() can reuse the block verbatim instead of rebuilding it.
        self.name_offsets: dict[str, int] = {}
        self.root = self._parse()

    def _str(self, off: int) -> str:
        end = self.strings.find(b"\x00", off)
        if end < 0:
            raise ValueError("unterminated string at %d" % off)
        return self.strings[off:end].decode("utf-8", "replace")

    def _parse(self) -> Node:
        buf = self.blob
        pos = self.off_struct
        end_struct = self.off_struct + self.size_struct
        root: Node | None = None
        stack: list[Node] = []
        while pos < end_struct:
            tok = u32be(buf, pos)
            pos += 4
            if tok == FDT_BEGIN_NODE:
                stop = buf.index(b"\x00", pos)
                name = buf[pos:stop].decode("utf-8", "replace")
                pos = pad4(stop + 1)
                node = Node(name)
                if stack:
                    stack[-1].children.append(node)
                else:
                    if root is not None:
                        raise ValueError("multiple root nodes")
                    root = node
                stack.append(node)
            elif tok == FDT_END_NODE:
                if not stack:
                    raise ValueError("unbalanced FDT_END_NODE")
                stack.pop()
            elif tok == FDT_PROP:
                ln = u32be(buf, pos)
                nameoff = u32be(buf, pos + 4)
                pos += 8
                val = buf[pos:pos + ln]
                pos = pad4(pos + ln)
                if not stack:
                    raise ValueError("property outside a node")
                nm = self._str(nameoff)
                self.name_offsets.setdefault(nm, nameoff)
                stack[-1].props.append((nm, val))
            elif tok == FDT_NOP:
                pass
            elif tok == FDT_END:
                break
            else:
                raise ValueError("unknown FDT token %d at 0x%x" % (tok, pos - 4))
        if stack or root is None:
            raise ValueError("malformed structure block")
        return root

    # ---- serialisation -------------------------------------------------
    def _emit(self, node: Node, out: bytearray, strmap: dict, strout: bytearray) -> None:
        name = node.name.encode("utf-8") + b"\x00"
        out += p32(FDT_BEGIN_NODE) + name + b"\x00" * (pad4(len(name)) - len(name))
        for nm, val in node.props:
            off = strmap.get(nm)
            if off is None:
                off = len(strout)
                strmap[nm] = off
                strout += nm.encode("utf-8") + b"\x00"
            out += p32(FDT_PROP) + p32(len(val)) + p32(off)
            out += val + b"\x00" * (pad4(len(val)) - len(val))
        for c in node.children:
            self._emit(c, out, strmap, strout)
        out += p32(FDT_END_NODE)

    def tobytes(self) -> bytes:
        # Reuse the original strings block verbatim and only append names that are
        # not already in it.  Rebuilding the block from scratch yields a larger one
        # than dtc's (which is tight), and the Archytas 32 MiB boot template has no
        # slack: a DTB even 110 bytes too big makes make_wifi_template.py emit a
        # 32 MiB + N image, and package_archimedes_boot.py then aborts with
        # "template must be an exact 32 MiB Android boot image".
        #
        # Note "shared-dma-pool" does NOT need a strings entry -- it is a property
        # *value* (it lives in the struct block inside the compatible property).
        # The only new property *name* is "memory-region" (+14 bytes), which is why
        # the result is exactly as large as the vendor's fixed-up 4G DTB (84094).
        strout = bytearray(self.strings)
        if strout and strout[-1] != 0:
            strout += b"\x00"
        strmap: dict[str, int] = dict(self.name_offsets)
        struct_out = bytearray()
        self._emit(self.root, struct_out, strmap, strout)
        struct_out += p32(FDT_END)

        out = bytearray(b"\x00" * 40)          # header placeholder
        while len(out) % 8:
            out += b"\x00"
        off_rsvmap = len(out)
        out += self.rsvmap
        while len(out) % 4:
            out += b"\x00"
        off_struct = len(out)
        out += struct_out
        while len(out) % 4:
            out += b"\x00"
        off_strings = len(out)
        out += strout
        # Deliberately NO trailing pad: the vendor's own fixed-up DTB is 84094
        # bytes (2 mod 4), so dtc/TheKit do not pad the blob either. Padding here
        # would push the total 2 bytes over the template's DTB and grow the boot
        # image past 32 MiB.

        struct.pack_into(
            ">IIIIIIIII",
            out, 0,
            FDT_MAGIC, len(out), off_struct, off_strings, off_rsvmap,
            self.version, self.last_comp_version, self.boot_cpuid_phys,
            len(strout),
        )
        struct.pack_into(">I", out, 36, len(struct_out))
        return bytes(out)


def find_path(root: Node, *parts: str) -> Node | None:
    cur = root
    for p in parts:
        cur = cur.child(p)
        if cur is None:
            return None
    return cur


def next_free_phandle(root: Node) -> int:
    used = set()
    for n in root.walk():
        for prop in ("phandle", "linux,phandle"):
            v = n.get(prop)
            if v is not None and len(v) >= 4:
                used.add(struct.unpack_from(">I", v, 0)[0])
    cand = 1
    while cand in used:
        cand += 1
    return cand


def patch(dtb: bytes) -> tuple[bytes, bool]:
    fdt = Fdt(dtb)
    rmem_parent = find_path(fdt.root, RESERVED_MEMORY_NODE)
    if rmem_parent is None:
        raise ValueError("no /reserved-memory node")

    node = rmem_parent.child(WIFI_RMEM_NODE)
    if node is None:
        # Some DTBs attach it with a unit address; accept that too.
        for c in rmem_parent.children:
            if c.name.split("@")[0] == WIFI_RMEM_NODE:
                node = c
                break
    if node is None:
        raise ValueError("no wifi-reserve-memory node under /reserved-memory")

    wifi = fdt.root.child(WIFI_NODE)
    if wifi is None:
        raise ValueError("no %s node at root" % WIFI_NODE)

    changed = False

    # Already patched?  (Checks both wifi- and consys-reserve-memory so the
    # function is idempotent when run on an already-fully-patched blob.)
    want_reg = struct.pack(">IIII", 0, WIFI_RMEM_BASE, 0, WIFI_RMEM_SIZE)
    wifi_already_patched = (
        node.get("compatible") == WIFI_RMEM_COMPAT
        and node.get("reg") == want_reg
        and node.get("phandle")
        and wifi.get("memory-region") == node.get("phandle")
    )

    if not wifi_already_patched:
        changed = True
        ph = node.get("phandle") or node.get("linux,phandle")
        if ph and len(ph) >= 4:
            phandle = struct.unpack_from(">I", ph, 0)[0]
        else:
            phandle = next_free_phandle(fdt.root)
        phval = p32(phandle)

        # reserved-memory/wifi-reserve-memory -> coherent DMA pool, fixed window.
        # "no-map" is deliberately KEPT: it is what makes rmem_cma_setup() bail out
        # and rmem_dma_setup() take over (see module docstring).
        node.drop("alloc-ranges", "compatible", "phandle", "linux,phandle")
        node.set("compatible", WIFI_RMEM_COMPAT)
        node.set("reg", want_reg)
        node.set("phandle", phval)

        # Cosmetic: mirror the property order used by the vendor 4G DTB so the
        # dumped result diffs cleanly against 4g_stock.dtb.
        order = ["phandle", "reg", "compatible", "no-map", "size", "alignment"]
        rank = {n: i for i, n in enumerate(order)}
        node.props.sort(key=lambda kv: rank.get(kv[0], len(order)))

        # wifi@18000000 -> bind the pool
        wifi.set("memory-region", phval)
        wifi_order = ["memory-region", "compatible", "reg", "interrupts"]
        wrank = {n: i for i, n in enumerate(wifi_order)}
        wifi.props.sort(key=lambda kv: wrank.get(kv[0], len(wifi_order)))

    # ---- CONSYS EMI window (shared memory for WMT STP) ------------------
    # Pin consys-reserve-memory to the same 0xbf000000 that the bootloader
    # and WMT STP firmware expect.  Without this the kernel's dynamic allocator
    # picks a different address inside 0x40000000..0xC0000000, the WMT driver
    # programs the CONSYS chip's EMI window to 0xbf000000 anyway, and the two
    # sides never agree on where the STP mailbox lives -> gRxCount=-1 and
    # every HOST_AWAKE / FW_START handshake times out.
    consys = rmem_parent.child(CONSYS_RMEM_NODE)
    if consys is None:
        for c in rmem_parent.children:
            if c.name.split("@")[0] == CONSYS_RMEM_NODE:
                consys = c
                break
    if consys is None:
        raise ValueError("no consys-reserve-memory node under /reserved-memory")

    # Property order for cosmetic mirroring (also used by consys).
    order = ["phandle", "reg", "compatible", "no-map", "size", "alignment"]
    rank = {n: i for i, n in enumerate(order)}

    consys_want_reg = struct.pack(">IIII", 0, CONSYS_RMEM_BASE, 0, CONSYS_RMEM_SIZE)
    consys_already_patched = (
        consys.get("reg") == consys_want_reg
        and consys.get("alloc-ranges") is None
    )
    if not consys_already_patched:
        consys.drop("alloc-ranges")
        consys.set("reg", consys_want_reg)
        consys.props.sort(key=lambda kv: rank.get(kv[0], len(order)))
        changed = True

    return fdt.tobytes(), changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    src = args.input.read_bytes()
    out, changed = patch(src)

    if not changed:
        print("already patched: %s" % args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out)

    # Re-parse to prove the result is a well-formed FDT with the right nodes.
    check = Fdt(out)
    rmem_root = find_path(check.root, RESERVED_MEMORY_NODE)
    rm = rmem_root.child(WIFI_RMEM_NODE)
    wf = check.root.child(WIFI_NODE)
    assert rm.get("compatible") == WIFI_RMEM_COMPAT, "wifi compatible not applied"
    assert rm.get("reg") == struct.pack(">IIII", 0, WIFI_RMEM_BASE, 0, WIFI_RMEM_SIZE), "wifi reg not applied"
    assert wf.get("memory-region") == rm.get("phandle"), "wifi memory-region not linked"
    assert rm.get("phandle") is not None
    cs = rmem_root.child(CONSYS_RMEM_NODE)
    assert cs is not None, "consys-reserve-memory missing"
    assert cs.get("reg") == struct.pack(">IIII", 0, CONSYS_RMEM_BASE, 0, CONSYS_RMEM_SIZE), "consys reg not applied"
    assert cs.get("alloc-ranges") is None, "consys alloc-ranges should have been dropped"
    assert cs.get("compatible") == CONSYS_RMEM_COMPAT, "consys compatible wrong"

    print("changed=%s" % changed)
    print("in  size=%d sha256=%s" % (len(src), hashlib.sha256(src).hexdigest()))
    print("out size=%d sha256=%s" % (len(out), hashlib.sha256(out).hexdigest()))
    print("wifi-reserve-memory: compatible=%r reg=%r phandle=%r"
          % (rm.get("compatible"),
             rm.get("reg").hex(),
             rm.get("phandle").hex()))
    print("wifi@18000000: memory-region=%r" % wf.get("memory-region").hex())
    print("consys-reserve-memory: compatible=%r reg=%r"
          % (cs.get("compatible"), cs.get("reg").hex()))


if __name__ == "__main__":
    main()
