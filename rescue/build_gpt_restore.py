#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""准备「还原原始 GPT」所需的两个写回件，供 mtkclient `wo` 使用。

为什么需要：resize_system_3gb.sh 改表前抓的 gpt_tail 裸备份失败了（TWRP 的 toybox dd
在偏移 ≥ 2^31 字节时报 Invalid argument 且静默不写），但同一时刻还留下两份**原始**来源：
  A) gpt_head_*.bin      —— 脚本用 dd 从 LBA 0 读的 34 扇区（MBR + 主头 + 32 项数组）
  B) gpt_backup_*.bin    —— sgdisk --backup 的产物，内含【主头】【备份头】【32 项数组】
本工具把两者交叉校验后，产出可直接 `wo` 的两个镜像：

  gpt_primary.bin   17408 字节  ->  wo 0            17408
     = 保护性 MBR(1) + 主 GPT 头(1) + 项数组(32)，正好对应磁盘 LBA 0..33
  gpt_backup.bin    16896 字节  ->  wo 15634251264  16896
     = 项数组(32) + 备份 GPT 头(1)，正好对应磁盘 LBA (DSZ-33)..(DSZ-1)
     注意备份头在 **DSZ-1**（即 30535679），不是 DSZ-33；entries_lba = DSZ-33。

全部结构都做 CRC32 自证 + 两源互证，任何一处不符即中止。
"""
import binascii
import os
import struct
import sys

SECTOR = 512
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "_diag", "resize_backup")
OUT = os.path.join(HERE, "gpt_restore")

TS = "20261006-110949"
PRIMARY_SRC = os.path.join(SRC, "gpt_head_%s.bin" % TS)
BACKUP_SRC = os.path.join(SRC, "gpt_backup_%s.bin" % TS)


def die(msg):
    print("!! " + msg, file=sys.stderr)
    sys.exit(1)


def crc32(b):
    return binascii.crc32(b) & 0xFFFFFFFF


def parse_header(b):
    """解析 512 字节 GPT 头块；顺带用 CRC32 自证（CRC 字段置零后重算）。"""
    if b[0:8] != b"EFI PART":
        die("GPT 头签名不对: %r" % b[0:8])
    rev, hsize, hcrc, _rsv = struct.unpack_from("<IIII", b, 8)
    my, alt, fu, lu = struct.unpack_from("<QQQQ", b, 0x18)
    disk_guid = b[0x38:0x48]
    el, ne, es, ec = struct.unpack_from("<QIII", b, 0x48)
    tmp = bytearray(b[:hsize])
    tmp[0x10:0x14] = b"\0\0\0\0"
    calc = crc32(bytes(tmp))
    if hsize != 92:
        die("header_size 应为 92，实际 %d" % hsize)
    return dict(rev=rev, hsize=hsize, hcrc=hcrc, crc_ok=(calc == hcrc), calc_hcrc=calc,
                my=my, alt=alt, fu=fu, lu=lu, disk_guid=disk_guid,
                el=el, ne=ne, es=es, ec=ec)


def main():
    for p in (PRIMARY_SRC, BACKUP_SRC):
        if not os.path.isfile(p):
            die("缺原始来源文件：" + p)

    prim = open(PRIMARY_SRC, "rb").read()
    bk = open(BACKUP_SRC, "rb").read()

    print("=== 来源 A：gpt_head 裸抓取 ===")
    print("  大小 %d 字节 = %d 扇区" % (len(prim), len(prim) / SECTOR))
    if len(prim) != 34 * SECTOR:
        die("应为 34 扇区（MBR+主头+32 项）")

    # 保护性 MBR
    if prim[510:512] != b"\x55\xaa":
        die("MBR 结束标志 0x55AA 缺失")
    if prim[0x1C2] != 0xEE:
        die("保护性分区类型应为 0xEE，实际 0x%02X" % prim[0x1C2])
    mbr_start, mbr_size = struct.unpack_from("<II", prim, 0x1C6)
    print("  保护性 MBR: type=0xEE start_lba=%d size=%d 扇区（覆盖 LBA %d..%d）"
          % (mbr_start, mbr_size, mbr_start, mbr_start + mbr_size - 1))

    ph = parse_header(prim[SECTOR:2 * SECTOR])
    print("  主头: my_lba=%d alternate=%d entries_lba=%d first_usable=%d last_usable=%d"
          % (ph["my"], ph["alt"], ph["el"], ph["fu"], ph["lu"]))
    print("  主头 CRC32 自证: %s" % ("OK" if ph["crc_ok"] else "MISMATCH(%08X vs %08X)"
                                % (ph["hcrc"], ph["calc_hcrc"])))
    if not ph["crc_ok"]:
        die("主头 CRC 不符")

    ent_bytes = ph["ne"] * ph["es"]
    ents = prim[ph["el"] * SECTOR:ph["el"] * SECTOR + ent_bytes]
    if len(ents) != ent_bytes:
        die("从裸抓取里取不到完整项数组")
    calc_ec = crc32(ents)
    print("  项数组 %d 字节 CRC32 自证: %s"
          % (ent_bytes, "OK" if calc_ec == ph["ec"] else "MISMATCH"))
    if calc_ec != ph["ec"]:
        die("项数组 CRC 不符")

    dsz = ph["alt"] + 1
    print("  => 磁盘总扇区 DSZ = %d (%.2f GiB)" % (dsz, dsz * SECTOR / 2 ** 30))
    if ph["lu"] != dsz - 34:
        die("last_usable(%d) != DSZ-34(%d)" % (ph["lu"], dsz - 34))

    print()
    print("=== 来源 B：sgdisk --backup ===")
    print("  大小 %d 字节 = %d 扇区" % (len(bk), len(bk) / SECTOR))
    bh_main = parse_header(bk[SECTOR:2 * SECTOR])
    bh_backup = parse_header(bk[2 * SECTOR:3 * SECTOR])
    bk_ents = bk[3 * SECTOR:3 * SECTOR + ent_bytes]

    print("  内含主头   : my_lba=%d alternate=%d entries_lba=%d CRC=%s"
          % (bh_main["my"], bh_main["alt"], bh_main["el"],
             "OK" if bh_main["crc_ok"] else "MISMATCH"))
    print("  内含备份头 : my_lba=%d alternate=%d entries_lba=%d CRC=%s"
          % (bh_backup["my"], bh_backup["alt"], bh_backup["el"],
             "OK" if bh_backup["crc_ok"] else "MISMATCH"))
    if not (bh_main["crc_ok"] and bh_backup["crc_ok"]):
        die("sgdisk 备份里的头 CRC 不符")

    print()
    print("=== 交叉校验 ===")
    checks = [
        ("主头块逐字节一致", prim[SECTOR:2 * SECTOR] == bk[SECTOR:2 * SECTOR]),
        ("项数组逐字节一致", ents == bk_ents),
        ("备份头 my_lba == DSZ-1", bh_backup["my"] == dsz - 1),
        ("备份头 alternate_lba == 1", bh_backup["alt"] == 1),
        ("备份头 entries_lba == DSZ-33", bh_backup["el"] == dsz - 33),
        ("备份头 ent_crc == 项数组 CRC", bh_backup["ec"] == calc_ec),
        ("备份头 disk_guid == 主头 disk_guid", bh_backup["disk_guid"] == ph["disk_guid"]),
        ("first/last_usable 一致", (bh_backup["fu"], bh_backup["lu"]) == (ph["fu"], ph["lu"])),
    ]
    for name, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "!!", name))
    if not all(ok for _, ok in checks):
        die("交叉校验未通过，拒绝产出写回件")

    # 分区清单（人眼确认这是扩容前的表）
    print()
    print("=== 这份表声称的分区布局（应为扩容前：system 1433 MiB / userdata 12090 MiB）===")
    for i in range(ph["ne"]):
        e = ents[i * ph["es"]:(i + 1) * ph["es"]]
        if e[0:16] == b"\0" * 16:
            continue
        ts_ = e[0:16]
        s, en = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16-le").rstrip("\0")
        print("  #%-3d %-12s %9d .. %-9d %9.1f MiB" % (i + 1, name, s, en,
                                                       (en - s + 1) * SECTOR / 2 ** 20))

    # ---------- 产出 ----------
    primary_img = prim                                     # LBA 0..33
    backup_img = bk_ents + bk[2 * SECTOR:3 * SECTOR]       # LBA DSZ-33..DSZ-1
    if len(backup_img) != 33 * SECTOR:
        die("备份镜像长度异常 %d" % len(backup_img))

    # 产出自证：从产出的字节里重新解析，看 CRC 是否还成立
    rp = parse_header(primary_img[SECTOR:2 * SECTOR])
    rb = parse_header(backup_img[-SECTOR:])
    rec_ec = crc32(backup_img[:-SECTOR])
    print()
    print("=== 产出自证 ===")
    print("  gpt_primary.bin : %d 字节 主头 CRC=%s 项数组 CRC=%s"
          % (len(primary_img), "OK" if rp["crc_ok"] else "MISMATCH",
             "OK" if crc32(primary_img[2 * SECTOR:]) == rp["ec"] else "MISMATCH"))
    print("  gpt_backup.bin  : %d 字节 备份头 CRC=%s 项数组 CRC=%s"
          % (len(backup_img), "OK" if rb["crc_ok"] else "MISMATCH",
             "OK" if rec_ec == rb["ec"] else "MISMATCH"))
    if not (rp["crc_ok"] and rb["crc_ok"] and rec_ec == rb["ec"]):
        die("产出件自证失败")

    os.makedirs(OUT, exist_ok=True)
    p1 = os.path.join(OUT, "gpt_primary.bin")
    p2 = os.path.join(OUT, "gpt_backup.bin")
    open(p1, "wb").write(primary_img)
    open(p2, "wb").write(backup_img)

    prim_off, prim_len = 0, len(primary_img)
    bk_off, bk_len = (dsz - 33) * SECTOR, len(backup_img)

    with open(os.path.join(OUT, "offsets.txt"), "w", encoding="utf-8") as f:
        f.write("# 用法： mtk wo <offset> <length> <file>\n")
        f.write("# 主表：保护性 MBR + 主 GPT 头 + 项数组  -> 磁盘 LBA 0..33\n")
        f.write("%d %d gpt_primary.bin\n" % (prim_off, prim_len))
        f.write("# 备份表：项数组(32 扇区) + 备份 GPT 头(1 扇区)  -> 磁盘 LBA (DSZ-33)..(DSZ-1)\n")
        f.write("%d %d gpt_backup.bin\n" % (bk_off, bk_len))
        f.write("# DSZ=%d sector_size=%d\n" % (dsz, SECTOR))

    print()
    print("产出目录: " + OUT)
    print("  gpt_primary.bin  %6d B   ->  mtk wo %-12d %-6d gpt_primary.bin"
          % (prim_len, prim_off, prim_len))
    print("  gpt_backup.bin   %6d B   ->  mtk wo %-12d %-6d gpt_backup.bin"
          % (bk_len, bk_off, bk_len))


if __name__ == "__main__":
    main()
