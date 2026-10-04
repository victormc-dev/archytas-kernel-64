#!/usr/bin/env python3
"""校验 archytas-wifi-boot 两份镜像的正确性（不依赖实体设备）。

检查项：
  1. 文件尺寸 == 32 MiB 且 magic == ANDROID!
  2. page_size == 2048 且 cmdline 含 bootopt=64S3,32S1,64S1
  3. 内核 gzip 解压后含 "Linux version 4.9.117"
  4. 内核区尾部 DTB：Stage1 应保持 4G 模板 DTB；Stage2 必须换成 Wi-Fi DTB
     （含 aw87329_pa，且 SHA256 与 wifi_stock.dtb 一致）
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BOOT_DIR = ROOT / "archytas-wifi-boot"
WIFI_DTB = ROOT / "port" / "wifi_stock.dtb"
TEMPLATE = ROOT / "archimedes-kernel-64-main" / "tools" / "archimedes-boot-template-32MiB.img"
# HANDOFF 记录的 archimedes 模板(4G) DTB SHA256 前缀
TEMPLATE_DTB_SHA = "7a5af1887a2ad6bdf2fb3e27d806a7d3914c2f7ba93fa781f3540b9ff77bf304"

PAGE = 2048
BOOT_SIZE = 32 * 1024 * 1024
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
EXPECT_CMD = b"bootopt=64S3,32S1,64S1"


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def trailing_fdt(blob: bytes) -> tuple[int, int]:
    pos = blob.rfind(FDT_MAGIC)
    while pos >= 0:
        if pos + 8 <= len(blob):
            size = struct.unpack_from(">I", blob, pos + 4)[0]
            if size >= 40 and pos + size == len(blob):
                return pos, size
        pos = blob.rfind(FDT_MAGIC, 0, pos)
    raise ValueError("no trailing FDT in kernel region")


def kernel_version(gzip_bytes: bytes) -> str:
    try:
        raw = zlib.decompress(gzip_bytes, 16 + zlib.MAX_WBITS)
    except Exception:
        do = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = do.decompress(gzip_bytes) + do.flush()
    idx = raw.find(b"Linux version")
    if idx < 0:
        return "(未找到)"
    end = raw.find(b"\x00", idx)
    end = end if end > idx else idx + 80
    return raw[idx:end].decode("ascii", "replace").strip()


def parse_image(path: Path) -> dict:
    d = path.read_bytes()
    assert len(d) == BOOT_SIZE, f"{path.name}: 尺寸 {len(d)} != 32MiB"
    assert d[:8] == b"ANDROID!", f"{path.name}: 非 Android boot 镜像"
    page = u32(d, 0x24) or 2048
    assert page == PAGE, f"{path.name}: page_size={page} != 2048"
    ks = u32(d, 8)
    rs = u32(d, 16)
    cmdline = d[0x40:0x40 + 512].rstrip(b"\x00").decode("ascii", "replace")
    region = d[PAGE:PAGE + ks]
    gz = region[: region.rfind(FDT_MAGIC)] if FDT_MAGIC in region else region
    off, size = trailing_fdt(region)
    dtb = region[off:off + size]
    return dict(
        path=path, page=page, ks=ks, rs=rs, cmdline=cmdline,
        kernel_ver=kernel_version(gz), dtb=dtb, dtb_sha=hashlib.sha256(dtb).hexdigest(),
        dtb_has_wifi=b"aw87329_pa" in dtb,
    )


def main() -> None:
    # Optional arguments: image paths to check INSTEAD of the two canonical ones
    # in archytas-wifi-boot/. Point them at a freshly unpacked CI artifact before
    # flashing it -- the canonical files may be from an older build, and nothing
    # says so on the filename.
    #   python3 port/validate_boot.py _ci/<run>/boot-archytas-wifi*.img
    targets = [Path(a) for a in sys.argv[1:]] or [
        BOOT_DIR / "boot-archytas-wifi-stage1.img",
        BOOT_DIR / "boot-archytas-wifi.img",
    ]

    wifi_ref = WIFI_DTB.read_bytes() if WIFI_DTB.exists() else b""
    wifi_sha = hashlib.sha256(wifi_ref).hexdigest() if wifi_ref else "(无参考文件)"

    # 模板(4G) DTB：优先用本地上游模板抽取，否则回退到 HANDOFF 记录的 SHA
    tpl_dtb_sha = TEMPLATE_DTB_SHA
    if TEMPLATE.exists():
        try:
            td = TEMPLATE.read_bytes()
            tks = u32(td, 8)
            tregion = td[PAGE:PAGE + tks]
            toff, tsize = trailing_fdt(tregion)
            tpl_dtb_sha = hashlib.sha256(tregion[toff:toff + tsize]).hexdigest()
            print(f"[基准] 从上游模板抽取 4G DTB SHA256: {tpl_dtb_sha}\n")
        except Exception as e:
            print(f"[警告] 无法从模板抽取 DTB({e})，改用 HANDOFF 记录 SHA\n")

    ok = True
    for p in targets:
        name = str(p)
        if not p.exists():
            print(f"[缺失] {name}")
            ok = False
            continue
        try:
            r = parse_image(p)
        except Exception as e:
            print(f"[解析失败] {name}: {e}")
            ok = False
            continue
        # Stage1 carries the 4G template DTB, Stage2 the Wi-Fi one. Decide by the
        # DTB itself rather than by the filename, so an arbitrary path works.
        is_stage1 = ("stage1" in name) or (r["dtb_sha"] == tpl_dtb_sha)
        stage = "Stage1(4G 模板)" if is_stage1 else "Stage2(Wi-Fi DTB)"
        cmd_ok = EXPECT_CMD in r["cmdline"].encode()
        if is_stage1:
            dtb_ok = (r["dtb_sha"] == tpl_dtb_sha)
            dtb_msg = f"== 模板(4G) DTB: {dtb_ok}"
        else:
            dtb_ok = (r["dtb_sha"] == wifi_sha) if wifi_ref else False
            dtb_msg = f"== wifi_stock.dtb: {dtb_ok}"
        ver_ok = r["kernel_ver"].startswith("Linux version 4.9.117")
        img_ok = (r["page"] == PAGE and len(p.read_bytes()) == BOOT_SIZE)
        all_ok = cmd_ok and dtb_ok and ver_ok and img_ok
        ok = ok and all_ok

        print(f"===== {name}  ({stage}) =====")
        print(f"  镜像格式 32MiB/v1/2048页: {img_ok}")
        print(f"  cmdline含bootopt: {cmd_ok}")
        print(f"  内核版本 4.9.117: {ver_ok}  -> {r['kernel_ver']}")
        print(f"  尾部DTB {dtb_msg}")
        print(f"  尾部DTB SHA256: {r['dtb_sha']}")
        print(f"  >>> 综合判定: {'PASS' if all_ok else 'FAIL'}\n")

    print(f"参考 wifi_stock.dtb SHA256: {wifi_sha}")
    print(f"参考 模板(4G) DTB SHA256: {tpl_dtb_sha}")
    print(f"\n==== 总判定: {'ALL PASS' if ok else '存在失败项，需排查'} ====")


if __name__ == "__main__":
    main()
