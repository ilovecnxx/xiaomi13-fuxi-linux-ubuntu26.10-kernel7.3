#!/usr/bin/env python3
"""厂商运行时 DTS（dtc 反编译文本）→ 每个 regulator-name 的 consumer 列表（节点路径 + 属性名）。
用法: vendor-supply-map.py fuxi-live.dts [名字过滤正则]"""
import re, sys, collections
path = sys.argv[1]; flt = re.compile(sys.argv[2]) if len(sys.argv) > 2 else None
stack, nodes = [], {}          # nodes: path -> dict(props)
cur = None
for line in open(path, encoding="utf-8", errors="replace"):
    s = line.strip()
    m = re.match(r'^([\w@,.\-+/]+|/)\s*\{$', s)
    if m:
        stack.append(m.group(1)); cur = "/" + "/".join(x for x in stack if x != "/")
        nodes.setdefault(cur, {}); continue
    if s.startswith("};"):
        stack.pop(); cur = "/" + "/".join(x for x in stack if x != "/") if stack else None; continue
    m = re.match(r'^([\w,.\-#+]+)\s*=\s*(.*);$', s)
    if m and cur is not None:
        nodes[cur][m.group(1)] = m.group(2)
ph2node = {}
for p, props in nodes.items():
    if "phandle" in props:
        ph2node[int(props["phandle"].strip("<>"), 16)] = p
cons = collections.defaultdict(list)
for p, props in nodes.items():
    for k, v in props.items():
        if k.endswith("-supply") and v.startswith("<"):
            for ph in re.findall(r'0x[0-9a-f]+', v):
                tgt = ph2node.get(int(ph, 16))
                if tgt:
                    cons[tgt].append((p, k))
for tgt, lst in sorted(cons.items()):
    name = nodes[tgt].get("regulator-name", "").strip('"')
    if not name or (flt and not flt.search(name)):
        continue
    users = sorted({f"{u.split('/')[-1]}:{k}" for u, k in lst if "proxy" not in k and "parent" not in k})
    print(f"{name}\t{nodes[tgt].get('regulator-min-microvolt','?').strip('<>')}\t" + ", ".join(users))
