#!/usr/bin/env python3
"""fuxi-graphopen-parse.py - 解析 GRAPH_OPEN(0x01001000) 的带外负载，并可与 q6voice-graphs.h 对比。

用法（python3 -I）:
  python3 -I scripts/fuxi-graphopen-parse.py dump BIN OFFSET SIZE
  python3 -I scripts/fuxi-graphopen-parse.py json BIN OFFSET SIZE > out.json
  python3 -I scripts/fuxi-graphopen-parse.py cmp BIN OFFSET SIZE q6voice-graphs.h tx|rx
负载是一串 {miid, param_id, size, err} + data（向上取 8）记录，必须恰好吃完 SIZE，否则退出 1。
"""
import json
import re
import struct
import sys

SG_CFG, CONT_CFG, MOD_LIST, MOD_PROP, MOD_CONN = 0x08001001, 0x08001000, 0x08001002, 0x08001003, 0x08001004
NAMES = {SG_CFG: 'SUB_GRAPH_CONFIG', CONT_CFG: 'CONTAINER_CONFIG', MOD_LIST: 'MODULE_LIST',
         MOD_PROP: 'MODULE_PROP', MOD_CONN: 'MODULE_CONN'}


def u32s(b, off, n):
    return list(struct.unpack_from('<%dI' % n, b, off))


def props(b, off, n):
    out = []
    for _ in range(n):
        pid, sz = u32s(b, off, 2)
        out.append((pid, u32s(b, off + 8, sz // 4)))
        off += 8 + sz
    return out, off


def parse(blob):
    recs, off = [], 0
    while off < len(blob):
        if off + 16 > len(blob):
            sys.exit('truncated header at 0x%x' % off)
        miid, pid, sz, err = u32s(blob, off, 4)
        data = blob[off + 16:off + 16 + sz]
        if len(data) != sz:
            sys.exit('record at 0x%x overruns (size 0x%x)' % (off, sz))
        recs.append((miid, pid, sz, err, data))
        off += 16 + ((sz + 7) & ~7)
    if off != len(blob):
        sys.exit('records end at 0x%x, size 0x%x' % (off, len(blob)))
    g = {'sgs': [], 'conts': [], 'mods': [], 'modprops': {}, 'conns': [], 'params': []}
    for miid, pid, sz, err, d in recs:
        if pid == SG_CFG:
            n, o = u32s(d, 0, 1)[0], 4
            for _ in range(n):
                sid, np_ = u32s(d, o, 2)
                p, o = props(d, o + 8, np_)
                g['sgs'].append({'sg': sid, 'props': p})
        elif pid == CONT_CFG:
            n, o = u32s(d, 0, 1)[0], 4
            for _ in range(n):
                cid, np_ = u32s(d, o, 2)
                p, o = props(d, o + 8, np_)
                g['conts'].append({'cont': cid, 'props': p})
        elif pid == MOD_LIST:
            n, o = u32s(d, 0, 1)[0], 4
            for _ in range(n):
                sid, cid, nm = u32s(d, o, 3)
                o += 12
                for _ in range(nm):
                    mid, mi = u32s(d, o, 2)
                    g['mods'].append({'sg': sid, 'cont': cid, 'mid': mid, 'miid': mi})
                    o += 8
        elif pid == MOD_PROP:
            n, o = u32s(d, 0, 1)[0], 4
            for _ in range(n):
                mi, np_ = u32s(d, o, 2)
                p, o = props(d, o + 8, np_)
                g['modprops'][mi] = p
        elif pid == MOD_CONN:
            n = u32s(d, 0, 1)[0]
            for i in range(n):
                g['conns'].append(tuple(u32s(d, 4 + 16 * i, 4)))
        else:
            g['params'].append({'miid': miid, 'param': pid, 'size': sz, 'data': d.hex()})
    return recs, g


def hx(v):
    return '0x%x' % v


def dump(recs, g):
    print('records: %d' % len(recs))
    for miid, pid, sz, err, _ in recs:
        print('  miid %-6s param %s %-17s size 0x%x err %d' % (hx(miid), hx(pid), NAMES.get(pid, ''), sz, err))
    for s in g['sgs']:
        print('SG %s %s' % (hx(s['sg']), ' '.join('%x=%s' % (p, v) for p, v in s['props'])))
    for c in g['conts']:
        print('CONT %s %s' % (hx(c['cont']), ' '.join('%x=%s' % (p, [hx(x) for x in v]) for p, v in c['props'])))
    for m in g['mods']:
        print('MOD sg %s cont %s mid %s miid %s props %s' % (hx(m['sg']), hx(m['cont']), hx(m['mid']), hx(m['miid']),
              g['modprops'].get(m['miid'])))
    for c in g['conns']:
        print('CONN %x:%d>%x:%d' % c)
    for p in g['params']:
        print('PARAM miid %s %s size 0x%x %s' % (hx(p['miid']), hx(p['param']), p['size'], p['data'][:96]))


def load_h(path, side):
    t = open(path).read()
    pre = 'q6voice_' + side

    def table(kind):
        m = re.search(r'%s_%s\[\] = \{(.*?)\};' % (pre, kind), t, re.S)
        return [[int(x, 0) for x in r.split(',') if x.strip()] for r in re.findall(r'\{([^{}]*)\}', m.group(1))]
    return table('sgs'), table('conts'), table('mods'), table('conns')


def cmp(g, hpath, side):
    sgs, conts, mods, conns = load_h(hpath, side)
    a_sg, o_sg = {s['sg'] for s in g['sgs']}, {s[0] for s in sgs}
    print('SG    android-only %s | ours-only %s | common %s' % (
        sorted(map(hx, a_sg - o_sg)), sorted(map(hx, o_sg - a_sg)), sorted(map(hx, a_sg & o_sg))))
    a_c, o_c = {c['cont'] for c in g['conts']}, {c[0] for c in conts}
    print('CONT  android-only %s | ours-only %s | common %s' % (
        sorted(map(hx, a_c - o_c)), sorted(map(hx, o_c - a_c)), sorted(map(hx, a_c & o_c))))
    a_m = {m['miid']: m for m in g['mods']}
    o_m = {m[1]: m for m in mods}
    print('MOD   android-only %s' % ['%x(%x,sg %x)' % (k, a_m[k]['mid'], a_m[k]['sg']) for k in sorted(set(a_m) - set(o_m))])
    print('      ours-only    %s' % ['%x(%x)' % (k, o_m[k][0]) for k in sorted(set(o_m) - set(a_m))])
    for k in sorted(set(a_m) & set(o_m)):
        am, om = a_m[k], o_m[k]
        pi = g['modprops'].get(k)
        ports = None
        for pid, v in pi or []:
            if pid == 0x08001015:
                ports = tuple(v[:2])
        diffs = []
        if am['mid'] != om[0]:
            diffs.append('mid %x vs %x' % (am['mid'], om[0]))
        if am['cont'] != conts[om[4]][0]:
            diffs.append('cont %x vs %x' % (am['cont'], conts[om[4]][0]))
        if ports and ports != (om[2], om[3]):
            diffs.append('ports %s vs %s' % (ports, (om[2], om[3])))
        if diffs:
            print('      diff %x: %s' % (k, '; '.join(diffs)))
    a_k, o_k = set(g['conns']), {tuple(c) for c in conns}
    print('CONN  android-only %s' % ['%x:%d>%x:%d' % c for c in sorted(a_k - o_k)])
    print('      ours-only    %s' % ['%x:%d>%x:%d' % c for c in sorted(o_k - a_k)])
    print('      common %d' % len(a_k & o_k))
    a_cp = {c['cont']: c['props'] for c in g['conts']}
    for c in conts:
        if c[0] in a_cp:
            ap = {p: v for p, v in a_cp[c[0]]}
            ours = {0x08001011: c[1], 0x08001012: c[2], 0x08001013: c[3], 0x08001014: c[4]}
            d = []
            for pid, ov in ours.items():
                av = ap.get(pid)
                if av is None:
                    d.append('%x missing' % pid)
                elif pid == 0x08001011:
                    if ov not in av[1:]:
                        d.append('cap %s vs %x' % ([hx(x) for x in av], ov))
                elif av[0] != ov:
                    d.append('%x %x vs %x' % (pid, av[0], ov))
            extra = sorted(set(ap) - set(ours))
            if d or extra:
                print('      cont %x: %s extra-props %s' % (c[0], '; '.join(d), [hx(x) for x in extra]))
    print('PARAMS in android graph-open (not in ours): %d' % len(g['params']))
    for p in g['params']:
        print('      miid %x param %x size 0x%x' % (p['miid'], p['param'], p['size']))


def main():
    mode, path, off, size = sys.argv[1], sys.argv[2], int(sys.argv[3], 0), int(sys.argv[4], 0)
    blob = open(path, 'rb').read()[off:off + size]
    recs, g = parse(blob)
    if mode == 'dump':
        dump(recs, g)
    elif mode == 'json':
        g['modprops'] = {hx(k): v for k, v in g['modprops'].items()}
        print(json.dumps(g, indent=1))
    elif mode == 'cmp':
        cmp(g, sys.argv[5], sys.argv[6])


main()
