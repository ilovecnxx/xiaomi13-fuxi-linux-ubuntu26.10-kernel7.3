#!/usr/bin/env python3
"""fuxi-acdb-dump.py - 离线只读解析 Qualcomm ACDB (Tutu_acdb_cal.acdb, 'ACDB' + 4字符tag分块)。

用法 (务必 python3 -I, 不要在数据目录里运行):
  python3 -I scripts/fuxi-acdb-dump.py FILE tags                 # 列 tag 块 (tag/偏移/长度)
  python3 -I scripts/fuxi-acdb-dump.py FILE aliases [子串]       # 列图别名 (GALS) + 键值
  python3 -I scripts/fuxi-acdb-dump.py FILE graph  <别名ID|子串>  # 图: SG/容器/模块/连线/控制链路/校准参数
  python3 -I scripts/fuxi-acdb-dump.py FILE sg 0xb000003e        # 某 SG 在 SGIT/CSLU/VC* 里的信息
  python3 -I scripts/fuxi-acdb-dump.py FILE modules              # MODM 模块表 (BT 编解码 so)
  加 --json 输出 JSON; graph 加 --no-cal 跳过校准参数, --cal-all 列每个 CKV 分支。

格式依据 (全部来自对 Tutu_acdb_cal.acdb 的字节对照, 见 docs/调研-ACDB语音图-20261101.md):
  文件 = 'ACDB'(4) u32=0 u32=总长-12, 然后重复 [tag(4) len(u32) data(len)]。
  GKVT/GKVL: 图键值(GKV) -> (A=SG邻接记录, B=图定义blob) 偏移 (POOL 内)。
  GALS: 别名 -> GKV。POOL: 所有 blob 的大池 (偏移均相对 POOL 起点)。
  图定义 blob = [size][nSG]{sg,len,payload}; SG payload = [hdr_len][SG头][rest_len][APM 参数块...]。
标注为 "未解析" 的部分只给出字节线索, 不做猜测。
"""
import sys
import struct
import json

U32 = struct.Struct('<I')


def u32(b, o):
    return U32.unpack_from(b, o)[0]


def h(x):
    return '0x%x' % x


MODULE_NAMES = {  # 来自 build/linux/sound/soc/qcom/qdsp6/audioreach.h (已知) , 其余为空 -> '?'
    0x07001000: 'WR_SHARED_MEM_EP', 0x07001001: 'RD_SHARED_MEM_EP', 0x07001002: 'GAIN',
    0x07001003: 'PCM_CNV', 0x07001004: 'PCM_ENC', 0x07001005: 'PCM_DEC',
    0x0700100A: 'I2S_SINK', 0x0700100B: 'I2S_SOURCE', 0x07001010: 'SAL',
    0x07001015: 'MFC', 0x0700101A: 'DATA_LOGGING', 0x07001023: 'CODEC_DMA_SINK',
    0x07001024: 'CODEC_DMA_SOURCE', 0x07001031: 'SMECNS_V2',
}
PARAM_NAMES = {
    0x08001000: 'CONTAINER_CONFIG', 0x08001001: 'SUB_GRAPH_CONFIG', 0x08001002: 'MODULE_LIST',
    0x08001003: 'MODULE_PROP', 0x08001004: 'MODULE_CONN', 0x08001015: 'PORT_INFO(in,out)',
    0x08001017: 'HW_EP_MF_CFG', 0x08001018: 'HW_EP_FRAME_SIZE_FACTOR', 0x08001026: 'MODULE_ENABLE',
    0x08001031: 'DATA_LOGGING_CONFIG', 0x0800100C: 'MEDIA_FORMAT', 0x08001063: 'CODEC_DMA_INTF_CFG',
    0x08001061: 'MODULE_CTRL_LINK(名称按位置推测)', 0x08001006: 'GAIN',
}
SG_PROP = {0x0800100E: 'perf(1=LP,2=LL)', 0x0800100F: 'dir(1=TX,2=RX)',
           0x08001010: 'scenario(1=play,2=rec,3=voice,-1=none)', 0x080010CC: 'prop_080010cc'}
CT_PROP = {0x08001011: 'cap_list', 0x08001012: 'graph_pos(1=stream,2=per-str-per-dev,3=str-dev,4=global-dev)',
           0x08001013: 'stack_size', 0x08001014: 'proc_domain(2=ADSP)', 0x080010CB: 'parent_cont',
           0x08001174: 'heap_id', 0x0800124D: 'prop_0800124d'}


def mname(mid):
    return MODULE_NAMES.get(mid, '?')


class Acdb:
    def __init__(self, path):
        d = open(path, 'rb').read()
        if d[:4] != b'ACDB':
            raise SystemExit('not ACDB')
        self.d = d
        self.total = u32(d, 8)
        self.tags = []
        o = 12
        while o + 8 <= len(d):
            t = d[o:o + 4].decode('latin1')
            n = u32(d, o + 4)
            self.tags.append((t, o + 8, n))
            o += 8 + n
        if o != len(d):
            print('WARN: tag chain ends at %d != file size %d' % (o, len(d)), file=sys.stderr)
        self.cache = {}

    def blk(self, t):
        for tag, off, n in self.tags:
            if tag == t:
                return self.d[off:off + n]
        return b''

    @property
    def pool(self):
        if 'pool' not in self.cache:
            self.cache['pool'] = self.blk('POOL')
        return self.cache['pool']

    def pool_ent(self, off):
        """POOL 条目 = [size u32][data size 字节][对齐填充] (CDDO 指向的数据按此读)"""
        p = self.pool
        n = u32(p, off)
        return p[off + 4:off + 4 + n]

    # ---- 图键值 ----
    def gkv_table(self):
        if 'gkvt' in self.cache:
            return self.cache['gkvt']
        g = self.blk('GKVT')
        n = u32(g, 0)
        p = 4
        tab = []
        for _ in range(n):
            nk, ne = struct.unpack_from('<II', g, p)
            p += 8
            for _ in range(ne):
                w = struct.unpack_from('<%dI' % (nk + 1), g, p)
                p += 4 * (nk + 1)
                tab.append((tuple(w[:nk]), w[nk]))  # (键序列, GKVL 内偏移)
        self.cache['gkvt'] = tab
        return tab

    def gkv_entries(self):
        """{(keys,values): (A偏移,B偏移)}  A=SG邻接记录, B=图定义 blob (均在 POOL)"""
        if 'gkvl' in self.cache:
            return self.cache['gkvl']
        L = self.blk('GKVL')
        ent = {}
        for keys, off in self.gkv_table():
            nk, ne = struct.unpack_from('<II', L, off)
            p = off + 8
            for _ in range(ne):
                w = struct.unpack_from('<%dI' % (nk + 2), L, p)
                p += 4 * (nk + 2)
                ent[(keys, tuple(w[:nk]))] = (w[nk], w[nk + 1])
        self.cache['gkvl'] = ent
        return ent

    def aliases(self):
        """GALS: [nGroups]{nk,ne}{ (key,val)*nk, 别名串偏移 }*ne ; 别名串 = [len][strlen]'ID | name\\0'"""
        if 'gals' in self.cache:
            return self.cache['gals']
        G = self.blk('GALS')
        P = self.pool
        n = u32(G, 0)
        p = 4
        out = []
        for _ in range(n):
            nk, ne = struct.unpack_from('<II', G, p)
            p += 8
            for _ in range(ne):
                w = struct.unpack_from('<%dI' % (2 * nk + 1), G, p)
                p += 4 * (2 * nk + 1)
                o = w[-1]
                sl = u32(P, o + 4)
                s = P[o + 8:o + 8 + sl].rstrip(b'\0').decode('latin1')
                idn, nm = s.split(' | ', 1)
                out.append({'id': int(idn), 'name': nm, 'keys': w[0:2 * nk:2], 'vals': w[1:2 * nk:2]})
        self.cache['gals'] = out
        return out


# ---------------- 图定义 blob 解析 ----------------

def read_props(b, p, n):
    """属性序列 {id, size, data(size字节)} * n -> ([(id, data)], 新位置)"""
    out = []
    for _ in range(n):
        pid, sz = struct.unpack_from('<II', b, p)
        out.append((pid, b[p + 8:p + 8 + sz]))
        p += 8 + sz
    return out, p


def words(b):
    return list(struct.unpack_from('<%dI' % (len(b) // 4), b)) if b else []


def dec_containers(d):
    n = u32(d, 0)
    p = 4
    res = []
    for _ in range(n):
        cid, np_ = struct.unpack_from('<II', d, p)
        props, p = read_props(d, p + 8, np_)
        res.append({'id': cid, 'props': [(CT_PROP.get(i, h(i)), words(v)) for i, v in props]})
    return res


def dec_modlist(d):
    n = u32(d, 0)
    p = 4
    res = []
    for _ in range(n):
        sg, cid, nm = struct.unpack_from('<III', d, p)
        p += 12
        mods = [struct.unpack_from('<II', d, p + 8 * i) for i in range(nm)]
        p += 8 * nm
        res.append({'sg': sg, 'cont': cid, 'mods': [{'mid': a, 'miid': b} for a, b in mods]})
    return res


def dec_modprops(d):
    n = u32(d, 0)
    p = 4
    res = {}
    for _ in range(n):
        miid, np_ = struct.unpack_from('<II', d, p)
        props, p = read_props(d, p + 8, np_)
        res[miid] = [(PARAM_NAMES.get(i, h(i)), words(v)) for i, v in props]
    return res


def dec_conns(d):
    n = u32(d, 0)
    return [struct.unpack_from('<IIII', d, 4 + 16 * i) for i in range(n)]


def dec_ctrl_links(d):
    """0x08001061: [n]{src_miid,src_port,dst_miid,dst_port,nprops,{id,size,data}}. 格式按字节形态推断。"""
    n = u32(d, 0)
    p = 4
    res = []
    for _ in range(n):
        s, sp, t, tp, np_ = struct.unpack_from('<IIIII', d, p)
        props, p = read_props(d, p + 20, np_)
        res.append({'src': s, 'src_port': sp, 'dst': t, 'dst_port': tp,
                    'props': [(h(i), words(v)) for i, v in props]})
    return res


def dec_sg_cfg(d):
    n = u32(d, 0)
    p = 4
    res = {}
    for _ in range(n):
        sg, np_ = struct.unpack_from('<II', d, p)
        props, p = read_props(d, p + 8, np_)
        res[sg] = [(SG_PROP.get(i, h(i)), words(v)) for i, v in props]
    return res


def dec_sg_x162(d):
    """0x08001162 (未解析): [n]{sg, nprops, {id,size,data}}; 内层 0x080011b2 = [cnt]{param_id, miid}。仅按形态列出"""
    n = u32(d, 0)
    p = 4
    res = []
    for _ in range(n):
        sg, np_ = struct.unpack_from('<II', d, p)
        props, p = read_props(d, p + 8, np_)
        out = []
        for i, v in props:
            w = words(v)
            if i == 0x080011b2 and w:
                out.append((h(i), [(h(w[1 + 2 * k]), h(w[2 + 2 * k])) for k in range(w[0])]))
            else:
                out.append((h(i), [h(x) for x in w]))
        res.append({'sg': sg, 'props': out})
    return res


def parse_sg(payload):
    hl = u32(payload, 0)
    hdr = payload[4:4 + hl]
    sg = u32(hdr, 0)
    hprops, _ = read_props(hdr, 8, u32(hdr, 4))
    rl = u32(payload, 4 + hl)
    rest = payload[8 + hl:8 + hl + rl]
    ok = (8 + hl + rl == len(payload))
    blocks = []
    gaps = []
    p = 0
    while p + 4 <= len(rest):
        w = u32(rest, p)
        if (w >> 16) == 0x0800 and p + 8 <= len(rest) and p + 8 + u32(rest, p + 4) <= len(rest):
            sz = u32(rest, p + 4)
            blocks.append((w, rest[p + 8:p + 8 + sz]))
            p += 8 + sz
        else:
            gaps.append((p, w))
            p += 4
    r = {'sg': sg, 'hdr_props_unparsed': [(h(i), [h(x) for x in words(v)]) for i, v in hprops],
         'len_ok': ok, 'gap_words': gaps, 'conts': [], 'modlist': [], 'modprops': {}, 'conns': [],
         'ctrl_links': [], 'sgcfg': {}, 'x162': [], 'other_blocks': []}
    for pid, d in blocks:
        if pid == 0x08001000:
            r['conts'] = dec_containers(d)
        elif pid == 0x08001001:
            r['sgcfg'] = dec_sg_cfg(d)
        elif pid == 0x08001002:
            r['modlist'] = dec_modlist(d)
        elif pid == 0x08001003:
            r['modprops'] = dec_modprops(d)
        elif pid == 0x08001004:
            r['conns'] = dec_conns(d)
        elif pid == 0x08001061:
            r['ctrl_links'] = dec_ctrl_links(d)
        elif pid == 0x08001162:
            r['x162'] = dec_sg_x162(d)
        else:
            r['other_blocks'].append((h(pid), len(d)))
    return r


def parse_blob(P, off):
    size = u32(P, off)
    n = u32(P, off + 4)
    p = off + 8
    sgs = []
    for _ in range(n):
        sg, sz = struct.unpack_from('<II', P, p)
        sgs.append(parse_sg(P[p + 8:p + 8 + sz]))
        p += 8 + sz
    return {'size': size, 'end_ok': (p == off + 4 + size), 'sgs': sgs}


def parse_adj(P, off):
    """A 记录 (SG 邻接): [size][n]{sg, k, k*sg}; 含义 (谁连谁) 按字节形态, 未验证方向"""
    n = u32(P, off + 4)
    p = off + 8
    res = {}
    for _ in range(n):
        sg, k = struct.unpack_from('<II', P, p)
        res[sg] = list(struct.unpack_from('<%dI' % k, P, p + 8))
        p += 8 + 4 * k
    return res


# ---------------- 全局 SG 表 / 校准 ----------------

def sgit(a):
    """SGIT: [n]{sg, k, k*{size, [x][nmods][(mid,miid)*]}} -> {sg: [(mid,miid)]}"""
    if 'sgit' in a.cache:
        return a.cache['sgit']
    b = a.blk('SGIT')
    n = u32(b, 0)
    p = 4
    res = {}
    for _ in range(n):
        sg, k = struct.unpack_from('<II', b, p)
        p += 8
        mods = []
        for _ in range(k):
            sz = u32(b, p)
            nm = u32(b, p + 8)
            mods += [struct.unpack_from('<II', b, p + 12 + 8 * i) for i in range(nm)]
            p += 4 + sz
        res[sg] = mods
    a.cache['sgit'] = res
    return res


def de_groups(b):
    """CDDE/VCDE: 顺序的 [n]{miid,param_id}*n 组, 返回 {字节偏移: [(miid,pid)]}"""
    o = 0
    res = {}
    while o + 4 <= len(b):
        n = u32(b, o)
        res[o] = [struct.unpack_from('<II', b, o + 4 + 8 * i) for i in range(n)]
        o += 4 + 8 * n
    return res


def do_group(b, o):
    n = u32(b, o)
    return list(struct.unpack_from('<%dI' % n, b, o + 4))


def cakt(a):
    """CAKT: 校准键集 (idx -> 键列表): [nk]{key}*nk 顺序排列"""
    w = words(a.blk('CAKT'))
    res = []
    p = 0
    while p < len(w):
        nk = w[p]
        res.append(tuple(w[p + 1:p + 1 + nk]))
        p += 1 + nk
    return res


def cal_records_cs(a, sg):
    """非语音校准: CSLU(sg->[(CAKT idx, CDLU偏移)]) -> CDLU 条目 [nk][nvals]{kv*nk,cdde_off,cddo_off,pool_off}*nvals"""
    C = a.blk('CSLU')
    D = a.blk('CDLU')
    DE = a.cache.setdefault('cdde', de_groups(a.blk('CDDE')))
    DO = a.blk('CDDO')
    n = u32(C, 0)
    p = 4
    ent = []
    for _ in range(n):
        s, k = struct.unpack_from('<II', C, p)
        p += 8
        if s == sg:
            ent += [struct.unpack_from('<II', C, p + 8 * j) for j in range(k)]
        p += 8 * k
    keysets = cakt(a)
    out = []
    for idx, off in ent:
        nk, nv = struct.unpack_from('<II', D, off)
        q = off + 8
        for _ in range(nv):
            kv = struct.unpack_from('<%dI' % nk, D, q)
            cdde, cddo, pool = struct.unpack_from('<III', D, q + 4 * nk)
            q += 4 * (nk + 3)
            pairs = DE[cdde]
            offs = do_group(DO, cddo)
            out.append({'src': 'CSLU/CDLU', 'keyset': keysets[idx] if idx < len(keysets) else None,
                        'kv': kv, 'cdde': cdde, 'cddo': cddo,
                        'params': [(m, pid, len(a.pool_ent(o))) for (m, pid), o in zip(pairs, offs)]})
    return out


def cal_records_vc(a, sg):
    """语音校准: VCCD = [4][0x08001163][nrec]{sg,size,payload}; payload=[1][0][x][nitems]{isize,A,isz,B,C,vcde_off,n,offs*n}
    A=VCKT 键集字节偏移(已核对), vcde_off/n 指向 VCDE 组 (已核对), B/C 疑为 VCLU 查表 (B=组合数, C=VCLU偏移, 未解析)。"""
    V = a.blk('VCCD')
    DE = a.cache.setdefault('vcde', de_groups(a.blk('VCDE')))
    KT = a.blk('VCKT')
    kts = {}
    o = 0
    while o < len(KT):
        nk = u32(KT, o)
        kts[o] = tuple(struct.unpack_from('<%dI' % nk, KT, o + 4))
        o += 4 + 4 * nk
    nrec = u32(V, 8)
    p = 12
    out = []
    for _ in range(nrec):
        s, sz = struct.unpack_from('<II', V, p)
        pl = V[p + 8:p + 8 + sz]
        p += 8 + sz
        if s != sg:
            continue
        ni = u32(pl, 12)
        q = 16
        for _ in range(ni):
            isz, A, isz2, B, C, vo, n = struct.unpack_from('<IIIIIII', pl, q)
            offs = struct.unpack_from('<%dI' % n, pl, q + 28)
            pairs = DE[vo]
            out.append({'src': 'VCCD/VCDE', 'keyset': kts.get(A), 'kv': None, 'vcde': vo,
                        'combos_B': B, 'vclu_off_C': C,
                        'params': [(m, pid, len(a.pool_ent(of))) for (m, pid), of in zip(pairs, offs)]})
            q += 4 + isz
    return out


def cal_for_sg(a, sg):
    return cal_records_cs(a, sg) + cal_records_vc(a, sg)


# ---------------- 图汇总 ----------------

def find_aliases(a, q):
    al = a.aliases()
    if q.isdigit():
        return [x for x in al if x['id'] == int(q)]
    ex = [x for x in al if x['name'] == q]
    return ex or [x for x in al if q in x['name']]


def build_graph(a, al, with_cal=True):
    ent = a.gkv_entries()[(al['keys'], al['vals'])]
    A, B = ent
    blob = parse_blob(a.pool, B)
    owner = {}  # miid -> (sg, mid) 全局 (SGIT)
    for sg, mods in sgit(a).items():
        for mid, miid in mods:
            owner.setdefault(miid, []).append((sg, mid))
    g = {'alias_id': al['id'], 'name': al['name'],
         'gkv': [(h(k), h(v)) for k, v in zip(al['keys'], al['vals'])],
         'A_off': h(A), 'B_off': h(B), 'blob_size': blob['size'], 'blob_end_ok': blob['end_ok'],
         'sg_adjacency_A': {h(k): [h(x) for x in v] for k, v in parse_adj(a.pool, A).items()},
         'sgs': []}
    inside = {}
    for s in blob['sgs']:
        for ml in s['modlist']:
            for m in ml['mods']:
                inside[m['miid']] = (s['sg'], m['mid'])
    for s in blob['sgs']:
        d = {'sg': h(s['sg']), 'len_ok': s['len_ok'], 'gap_words': s['gap_words'],
             'hdr_props_unparsed': s['hdr_props_unparsed'], 'sgcfg': s['sgcfg'].get(s['sg'], []),
             'containers': s['conts'], 'modlist': s['modlist'], 'modprops': s['modprops'],
             'conns': [], 'ctrl_links': s['ctrl_links'], 'x162_unparsed': s['x162'],
             'other_blocks': s['other_blocks']}
        for sm, sp, dm, dp in s['conns']:
            d['conns'].append({'src_miid': sm, 'src_port': sp, 'dst_miid': dm, 'dst_port': dp,
                               'src_in_graph': sm in inside, 'dst_in_graph': dm in inside})
        if with_cal:
            recs = cal_for_sg(a, s['sg'])
            d['cal_records'] = recs
        g['sgs'].append(d)
    g['global_owner'] = {h(k): [(h(sg), h(mid)) for sg, mid in v] for k, v in owner.items()}
    g['_inside'] = inside
    g['_owner'] = owner
    return g


def mod_label(g, miid):
    mid = g['_inside'].get(miid, (None, None))[1]
    if mid is None:
        own = g['_owner'].get(miid)
        if own:
            return '%s(SGIT:%s %s)' % (h(miid), h(own[0][0]), h(own[0][1]))
        return '%s(?不在本图/SGIT)' % h(miid)
    return '%s[mid=%s %s]' % (h(miid), h(mid), mname(mid))


def render(g, cal_all=False):
    o = []
    P = o.append
    P('== alias %d : %s' % (g['alias_id'], g['name']))
    P('GKV: ' + ' '.join('%s=%s' % kv for kv in g['gkv']))
    P('POOL blob: A(SG邻接)=%s  B(图定义)=%s  size=%d  结构自洽=%s' % (g['A_off'], g['B_off'], g['blob_size'], g['blob_end_ok']))
    P('SG 邻接记录 A (方向含义未验证): ' + '; '.join('%s: %s' % (k, ','.join(v) or '-') for k, v in g['sg_adjacency_A'].items()))
    for d in g['sgs']:
        P('')
        P('--- SG %s  (块长度自洽=%s, 未识别的游离字=%d)' % (d['sg'], d['len_ok'], len(d['gap_words'])))
        P('  SG 属性: ' + '; '.join('%s=%s' % (k, [h(x) if x > 9 else x for x in v]) for k, v in d['sgcfg']))
        P('  SG 头内属性(0x0800000x, 未解析): ' + str(d['hdr_props_unparsed']))
        for c in d['containers']:
            P('  容器 %s: ' % h(c['id']) + '; '.join('%s=%s' % (k, [h(x) for x in v]) for k, v in c['props']))
        for ml in d['modlist']:
            P('  容器 %s 模块: ' % h(ml['cont']) + ', '.join('%s(%s)' % (h(m['miid']), '%s:%s' % (h(m['mid']), mname(m['mid']))) for m in ml['mods']))
        for miid, pr in d['modprops'].items():
            P('  模块属性 %s: ' % h(miid) + '; '.join('%s=%s' % (k, [h(x) for x in v]) for k, v in pr))
        for c in d['conns']:
            P('  连线 %s:%s -> %s:%s%s' % (mod_label(g, c['src_miid']), h(c['src_port']), mod_label(g, c['dst_miid']), h(c['dst_port']),
                                          '' if c['src_in_graph'] and c['dst_in_graph'] else '   [端点不在本图]'))
        for c in d['ctrl_links']:
            P('  控制链路(0x08001061, 字段含义为推测) %s:%s -> %s:%s props=%s' % (h(c['src']), h(c['src_port']), h(c['dst']), h(c['dst_port']), c['props']))
        if d['x162_unparsed']:
            P('  0x08001162(未解析, 仅列形态): %s' % d['x162_unparsed'])
        if d['other_blocks']:
            P('  其他未解析块: %s' % d['other_blocks'])
        recs = d.get('cal_records')
        if recs is not None:
            agg = {}
            for r in recs:
                for m, pid, ln in r['params']:
                    agg.setdefault(m, {}).setdefault(pid, set()).add(ln)
            P('  校准记录数=%d (来源 %s)' % (len(recs), sorted(set(r['src'] for r in recs)) or '无'))
            for m in sorted(agg):
                P('    cal %s: ' % mod_label(g, m) + ', '.join('%s=%s字节' % (h(pid), '/'.join(str(x) for x in sorted(ls))) for pid, ls in sorted(agg[m].items())))
            if cal_all:
                for r in recs:
                    P('    [记录 %s keyset=%s kv=%s] %d 个参数' % (r['src'], [h(x) for x in (r['keyset'] or ())], r['kv'], len(r['params'])))
    return '\n'.join(o)


NOTES = {
    'HEAD': '文件头: 版本等', 'GKVT': '图键序列表(按键个数分组)', 'GKVL': 'GKV->(A,B) POOL偏移',
    'CSLU': 'SG->校准键集+CDLU偏移', 'CAKT': '校准键集表', 'CDLU': '校准查表(->CDDE/CDDO/POOL)',
    'CDDE': '(miid,param)组', 'CDDO': '参数数据在POOL的偏移组', 'SCLU': '(未解析) 另一套SG查表',
    'SCDE': '(未解析)', 'SCDO': '(未解析)', 'MTKT': '(未解析) 模块 tag 键表', 'MTLU': '(未解析)',
    'MTDE': '(未解析) (miid?,mid,tag) 对', 'MTDO': '(未解析)', 'TMLU': '(未解析)', 'TMDE': '(未解析) (mid,tag)',
    'PPTC': '(未解析) 5个 0x080011d4 ...', 'SGIT': 'SG->模块(mid,miid)表', 'POOL': '大池(图blob/参数数据/别名串)',
    'MODM': 'BT 编解码动态模块(so名)', 'BTUP': '(未解析)', 'VCCD': '语音校准记录(SG->items)',
    'VCMK': '(未解析) 语音键值', 'VCKT': '语音键集表', 'VCLU': '(未解析) 语音查表', 'VCDE': '语音(miid,param)组',
    'MTKL': '(未解析)', 'GPRO': '(未解析)', 'HASI': '(未解析)', 'GALS': '别名->GKV',
}


def main(argv):
    as_json = '--json' in argv
    argv = [x for x in argv if x != '--json']
    no_cal = '--no-cal' in argv
    cal_all = '--cal-all' in argv
    argv = [x for x in argv if x not in ('--no-cal', '--cal-all')]
    if len(argv) < 2:
        print(__doc__)
        return 2
    a = Acdb(argv[0])
    cmd = argv[1]
    rest = argv[2:]
    if cmd == 'tags':
        rows = [{'tag': t, 'payload_off': o, 'len': n, 'note': NOTES.get(t, '')} for t, o, n in a.tags]
        if as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print('ACDB 文件长 %d, 头部 total=%d (=文件长-12: %s)' % (len(a.d), a.total, a.total == len(a.d) - 12))
            for r in rows:
                print('%-5s 偏移 %8d (0x%07x) 长 %8d  %s' % (r['tag'], r['payload_off'], r['payload_off'], r['len'], r['note']))
    elif cmd == 'aliases':
        q = rest[0] if rest else ''
        rows = [x for x in a.aliases() if q in x['name']]
        for x in rows:
            print('%4d %s  %s' % (x['id'], x['name'], ' '.join('%s=%s' % (h(k), h(v)) for k, v in zip(x['keys'], x['vals']))))
        print('(%d 条)' % len(rows), file=sys.stderr)
    elif cmd == 'graph':
        if not rest:
            print('graph 需要别名ID或名称子串')
            return 2
        als = find_aliases(a, rest[0])
        if not als:
            print('无匹配别名')
            return 1
        if len(als) > 1 and not as_json:
            print('匹配 %d 条, 只展开第一条; 其余:' % len(als))
            for x in als[1:]:
                print('  %d %s' % (x['id'], x['name']))
        g = build_graph(a, als[0], with_cal=not no_cal)
        if as_json:
            g.pop('_inside')
            g.pop('_owner')
            print(json.dumps(g, ensure_ascii=False, indent=1, default=h))
        else:
            print(render(g, cal_all))
    elif cmd == 'sg':
        sg = int(rest[0], 0)
        print('SGIT 模块:', [(h(mid), mname(mid), h(miid)) for mid, miid in sgit(a).get(sg, [])])
        recs = cal_for_sg(a, sg)
        print('校准记录 %d' % len(recs))
        for r in recs:
            print(' ', r['src'], 'keyset=', [h(x) for x in (r['keyset'] or ())], 'kv=', r['kv'],
                  [(h(m), h(p), n) for m, p, n in r['params']])
    elif cmd == 'modules':
        b = a.blk('MODM')
        print('MODM 长 %d; 字符串:' % len(b))
        import re
        for s in re.findall(rb'[A-Za-z0-9_.]{6,}', b):
            print(' ', s.decode())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
