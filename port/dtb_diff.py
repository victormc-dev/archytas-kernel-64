#!/usr/bin/env python3
"""节点级全量对比两个 DTB（4G 模板 vs Wi-Fi 版），输出真实设备树差异。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dtb_dump import dump

def parse(dts_text):
    """返回 {节点路径: {prop名: 值串}} 与 {'_order':[路径...]}。"""
    tree = {}
    order = []
    stack = []
    cur = "/"
    for raw in dts_text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if s.startswith("/dts-v1/") or s.startswith("/memreserve/"):
            continue
        if s.endswith("{"):
            name = s[:-1].strip()
            if name == "/":
                cur = "/"
            else:
                stack.append(name)
                cur = "/" + "/".join(stack)
            if cur not in tree:
                tree[cur] = {}
                order.append(cur)
        elif s == "};":
            if stack:
                stack.pop()
                cur = "/" if not stack else "/" + "/".join(stack)
        else:
            if "=" in s:
                k, v = s.split("=", 1)
                k = k.strip(); v = v.strip().rstrip(";").strip()
            else:
                k = s.rstrip(";").strip(); v = ""
            tree.setdefault(cur, {})[k] = v
    return tree, order

def main():
    a_path, b_path = sys.argv[1], sys.argv[2]
    ta = dump(a_path); tb = dump(b_path)
    A, oa = parse(ta); B, ob = parse(tb)
    sa = set(A); sb = set(B)
    only_a = sorted(sa - sb)
    only_b = sorted(sb - sa)
    common = sorted(sa & sb)

    status_diff = []
    compat_diff = []
    reg_diff = []
    prop_diff = []   # 其它属性差异
    for p in common:
        pa, pb = A[p], B[p]
        if pa.get("status") != pb.get("status"):
            status_diff.append((p, pa.get("status"), pb.get("status")))
        if pa.get("compatible") != pb.get("compatible"):
            compat_diff.append((p, pa.get("compatible"), pb.get("compatible")))
        if pa.get("reg") != pb.get("reg"):
            reg_diff.append((p, pa.get("reg"), pb.get("reg")))
        # 其余 prop 差异（排除上面已列的）
        keys = set(pa) | set(pb)
        for k in keys:
            if k in ("status", "compatible", "reg"):
                continue
            if pa.get(k) != pb.get(k):
                prop_diff.append((p, k, pa.get(k), pb.get(k)))

    def fmt(v, n=60):
        if v is None: return "<none>"
        v = str(v)
        return v if len(v) <= n else v[:n] + "..."
    L = []
    L.append("=" * 70)
    L.append("DTB 全量对比:  4G模板(%s)  vs  Wi-Fi版(%s)" % (os.path.basename(a_path), os.path.basename(b_path)))
    L.append("=" * 70)
    L.append("4G 节点数: %d | Wi-Fi 节点数: %d | 共同: %d" % (len(sa), len(sb), len(common)))
    L.append("")
    L.append("【1】仅在 4G 模板出现 (%d):" % len(only_a))
    for p in only_a:
        L.append("    - " + p)
    L.append("")
    L.append("【2】仅在 Wi-Fi 版出现 (%d):" % len(only_b))
    for p in only_b:
        L.append("    + " + p)
    L.append("")
    L.append("【3】status 开关差异 (%d):" % len(status_diff))
    for p, va, vb in status_diff:
        L.append("    %s : 4G=%s  WiFi=%s" % (p, fmt(va), fmt(vb)))
    L.append("")
    L.append("【4】compatible 差异 (%d):" % len(compat_diff))
    for p, va, vb in compat_diff:
        L.append("    %s" % p)
        L.append("        4G:   %s" % fmt(va))
        L.append("        WiFi: %s" % fmt(vb))
    L.append("")
    L.append("【5】reg(地址) 差异 (%d):" % len(reg_diff))
    for p, va, vb in reg_diff:
        L.append("    %s" % p)
        L.append("        4G:   %s" % fmt(va))
        L.append("        WiFi: %s" % fmt(vb))
    L.append("")
    L.append("【6】其它属性差异 (%d) (截断显示前80项):" % len(prop_diff))
    for p, k, va, vb in prop_diff[:80]:
        L.append("    %s . %s : 4G=%s  WiFi=%s" % (p, k, fmt(va, 40), fmt(vb, 40)))
    if len(prop_diff) > 80:
        L.append("    ... (其余 %d 项略)" % (len(prop_diff) - 80))
    out = "\n".join(L)
    if len(sys.argv) > 3:
        dst = sys.argv[3]
    else:
        dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dtb_diff_report.txt")
    open(dst, "w").write(out)
    print(out)
    print("\n[已写入]", dst)

if __name__ == "__main__":
    main()
