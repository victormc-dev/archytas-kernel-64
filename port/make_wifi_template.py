#!/usr/bin/env python3
"""Swap the board DTB inside the Archimedes 32 MiB MTK boot template.

The Archimedes packager (package_archimedes_boot.py) keeps the template's
trailing DTB and only replaces the gzip kernel. To boot the *same* MT6761
kernel on the Wi-Fi board we just need a template whose trailing DTB
describes the Wi-Fi (Archytas) hardware.

This script takes the proven 4G template, finds its trailing board DTB in the
kernel region, and replaces it with the Wi-Fi stock DTB extracted from the
Wi-Fi ROM. It keeps the MTK v1 header, command line, ramdisk and the exact
32 MiB size untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

PAGE = 2048
BOOT_SIZE = 32 * 1024 * 1024
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def trailing_fdt(blob: bytes) -> tuple[int, int]:
    """Return (offset_within_blob, size) of the last DTB in blob."""
    pos = blob.rfind(FDT_MAGIC)
    while pos >= 0:
        if pos + 8 <= len(blob):
            size = struct.unpack_from(">I", blob, pos + 4)[0]
            if size >= 40 and pos + size == len(blob):
                return pos, size
        pos = blob.rfind(FDT_MAGIC, 0, pos)
    raise ValueError("no trailing FDT found in template kernel region")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, type=Path,
                    help="archimedes 32MiB boot template (4G DTB inside)")
    ap.add_argument("--wifi-dtb", required=True, type=Path,
                    help="extracted Wi-Fi stock DTB (wifi_stock.dtb)")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    tpl = args.template.read_bytes()
    wifi = args.wifi_dtb.read_bytes()

    if len(tpl) != BOOT_SIZE or tpl[:8] != b"ANDROID!":
        raise ValueError("template must be an exact 32 MiB Android boot image")
    if u32(tpl, 36) != PAGE:
        raise ValueError("template page size is not 2048")
    if b"aw87329_pa" not in wifi:
        raise ValueError("Wi-Fi DTB missing aw87329_pa node "
                         "(wrong board DTB? expected Archytas/MT6761)")

    ks = u32(tpl, 8)                       # template kernel region size
    region = tpl[PAGE:PAGE + ks]
    off4g, size4g = trailing_fdt(region)    # 4G DTB location in region
    gzip_part = tpl[PAGE:PAGE + off4g]      # kernel gzip, before the 4G DTB
    new_region = gzip_part + wifi           # kernel gzip + Wi-Fi DTB

    out = bytearray(BOOT_SIZE)
    out[:PAGE] = tpl[:PAGE]                 # MTK v1 header + cmdline + addrs
    out[PAGE:PAGE + len(new_region)] = new_region
    # Preserve everything that lived after the original kernel region
    # (ramdisk + padding) so the image layout stays identical except the DTB.
    tail = tpl[PAGE + ks:]
    out[PAGE + len(new_region):PAGE + len(new_region) + len(tail)] = tail
    struct.pack_into("<I", out, 8, len(new_region))

    # Self-check: re-extract the DTB the packager will see.
    noff, nsize = trailing_fdt(out[PAGE:PAGE + len(new_region)])
    assert out[PAGE + noff:PAGE + noff + nsize] == wifi, "DTB swap mismatch"
    assert b"aw87329_pa" in wifi

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out)
    print(f"output={args.output} size={len(out)}")
    print(f"wifi_dtb_size={nsize}")
    print(f"wifi_dtb_sha256={hashlib.sha256(wifi).hexdigest()}")


if __name__ == "__main__":
    main()
