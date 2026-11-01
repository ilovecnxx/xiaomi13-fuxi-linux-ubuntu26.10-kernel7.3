#!/usr/bin/python3
# 解码 strace 里 fd 8（audio_pkt GPR 通道）的 write/read 包。只读本地文件，不输出号码。
# 用法：fuxi-gpr-decode.py <strace文件> [summary | list <起始HH:MM:SS.fff> <结束> | dump <序号>]
# GPR 包头（24 字节，小端）：u32 ver:4 hdr_size:4 pkt_size:24；u8 dst_dom, src_dom；u16 reserved；
#   u32 src_port, dst_port, token, opcode；之后是负载。
import re
import struct
import sys
from collections import Counter

LINE = re.compile(r'^(\d+)\s+(\d\d:\d\d:\d\d\.\d+)\s+(write|read)\(8, "((?:\\x[0-9a-f]{2}|[^"\\])*)"')


def parse(path):
    out = []
    for ln in open(path, encoding="latin-1"):
        m = LINE.match(ln)
        if not m:
            continue
        pid, ts, op, body = m.groups()
        raw = bytes(int(h, 16) for h in re.findall(r"\\x([0-9a-f]{2})", body)) if "\\x" in body else body.encode("latin-1")
        # strace 把可打印字符直接输出；-x 模式下全是 \xNN，所以上面的分支就够用
        if len(raw) < 24:
            continue
        w0, = struct.unpack_from("<I", raw, 0)
        dst_dom, src_dom = raw[4], raw[5]
        src_port, dst_port, token, opcode = struct.unpack_from("<IIII", raw, 8)
        out.append({"i": len(out), "ts": ts, "op": op, "size": w0 >> 8, "hdr": (w0 >> 4) & 0xF,
                    "dst_dom": dst_dom, "src_dom": src_dom, "src": src_port, "dst": dst_port,
                    "token": token, "opcode": opcode, "payload": raw[(w0 >> 4 & 0xF) * 4:w0 >> 8]})
    return out


def main():
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "summary"
    pk = parse(path)
    if mode == "summary":
        print("packets", len(pk))
        c = Counter((p["op"], f'0x{p["opcode"]:08x}') for p in pk)
        for (op, oc), n in sorted(c.items(), key=lambda x: -x[1])[:25]:
            print(f"{n:5d} {op:5s} {oc}")
    elif mode == "list":
        a, b = sys.argv[3], sys.argv[4]
        for p in pk:
            if a <= p["ts"] <= b and p["op"] == "write":
                print(f'#{p["i"]} {p["ts"]} dom{p["src_dom"]}->{p["dst_dom"]} port {p["src"]:#x}->{p["dst"]:#x} '
                      f'tok={p["token"]:#x} op={p["opcode"]:#010x} len={len(p["payload"])}')
    elif mode == "cfg":
        # APM_CMD_SET_CFG 带内负载：12 字节地址/内存句柄 + u32 总长 + 若干 {module_iid, param_id, size, err, data[size 向上取 8]}
        only = int(sys.argv[3], 0) if len(sys.argv) > 3 else None
        for p in pk:
            if p["op"] != "write" or p["opcode"] != 0x01001006 or len(p["payload"]) <= 16:
                continue
            d, total, o = p["payload"], struct.unpack_from("<I", p["payload"], 12)[0], 16
            if total + 16 > len(d) or d[0:12] != bytes(12):
                print(f'#{p["i"]} {p["ts"]} port {p["dst"]:#x} out-of-band or odd layout, len={len(d)}')
                continue
            while o + 16 <= 16 + total:
                mod, pid, sz, err = struct.unpack_from("<IIII", d, o)
                data = d[o + 16:o + 16 + sz]
                if only is None or mod == only:
                    print(f'#{p["i"]} {p["ts"]} gh={p["src"]:#x} mod={mod:#x} param={pid:#010x} size={sz} data={data[:48].hex(" ")}')
                o += 16 + ((sz + 7) & ~7)
    elif mode == "dump":
        p = pk[int(sys.argv[3])]
        print(f'{p["ts"]} op={p["opcode"]:#010x} len={len(p["payload"])}')
        d = p["payload"]
        for o in range(0, len(d), 16):
            print(f"{o:04x}  " + " ".join(f"{x:02x}" for x in d[o:o + 16]))


main()
