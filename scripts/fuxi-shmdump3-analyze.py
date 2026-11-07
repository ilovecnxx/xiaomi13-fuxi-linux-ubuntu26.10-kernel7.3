#!/usr/bin/env python3
"""分析 voice-shmdump3 抓包：定位每条 0x01001008 校准块在哪个 map 文件、偏移、大小。

对每条 0x01001008：
  payload[0:4]  = dmabuf 内偏移
  payload[8:16] = DSP IOVA 指针
在 packet 对应的所有 map 文件里，从该偏移读记录链 {miid,pid,size,err,data(8对齐)}，
找到能吃满到一个合理结尾的 map，报告 records 数与总大小。
"""
import os, struct, sys, glob, re

def parse_chain(name, data, off):
    """从 data[off] 读记录链，返回 (nrec, total_bytes) 或 None。"""
    o = off
    n = 0
    while o + 16 <= len(data):
        miid, pid, sz, err = struct.unpack_from("<IIII", data, o)
        if err != 0:
            return None
        if sz == 0 or sz > 0x40000:
            return None
        nxt = o + 16 + ((sz + 7) & ~7)
        if nxt > len(data):
            return None
        o = nxt
        n += 1
        # 合理的链不会超过 256 条
        if n > 256:
            return None
    return (n, o - off) if n > 0 else None

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "work/claude-logs/voice-shmdump3-20261102/voice-shmdump3"
    log = open(os.path.join(src, "packets.log")).read()
    # 找所有 0x01001008 行
    pat = re.compile(r'#(\d+) .* op=01001008 src=([0-9a-f]+) dst=([0-9a-f]+) tok=([0-9a-f]+) len=(\d+) payload: ([0-9a-f ]+)')
    for m in pat.finditer(log):
        pkt, srcid, dst, tok, plen, pload = m.groups()
        pload = bytes(int(x, 16) for x in pload.split())
        off_field = struct.unpack_from("<I", pload, 0)[0]
        iova = struct.unpack_from("<Q", pload, 8)[0]
        # 找该 packet 的所有 map 文件
        maps = sorted(glob.glob(os.path.join(src, f"pkt{int(pkt):04d}-op01001008-map*.bin")))
        print(f"\n#{pkt} src=0x{srcid} tok=0x{tok} off_field=0x{off_field:04x} iova=0x{iova:012x} maps={len(maps)}")
        for mf in maps:
            data = open(mf, "r+b").read()
            fn = os.path.basename(mf)
            r = parse_chain(f"#{pkt}", data, off_field)
            if off_field < len(data):
                miid0, pid0, sz0, err0 = struct.unpack_from("<IIII", data, off_field)
            else:
                miid0 = pid0 = sz0 = err0 = -1
            tag = "CHAIN OK" if r else "no chain"
            print(f"  {fn} size={len(data):#x} -> first_rec miid=0x{miid0:08x} pid=0x{pid0:08x} sz=0x{sz0:x} err=0x{err0:x} {tag}")
            if r:
                print(f"     => records={r[0]} total=0x{r[1]:x} ({r[1]} bytes)")

if __name__ == "__main__":
    main()
