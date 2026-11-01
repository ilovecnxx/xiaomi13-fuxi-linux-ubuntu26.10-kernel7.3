// SPDX-License-Identifier: GPL-2.0
/*
 * Voice call graphs for q6apm (EXPERIMENTAL, NOT FUNCTIONAL YET).
 *
 * Builds the TX and RX voice graphs at runtime from tables generated out of
 * the vendor ACDB (scripts/fuxi-voice-graph-gen.py) and drives the VCPM
 * parameters seen in an Android VoLTE capture.
 *
 * Known gaps (see docs/设计-内核语音子图-20261101.md):
 *  - CODEC_DMA endpoint configuration (HW_EP_MF_CFG / INTF_CFG) is NOT sent;
 *    the interface values are not recoverable from the ACDB offline.
 *  - Relative order of the VCPM SET_CFGs vs. prepare/start is a guess.
 *  - Graph-external connections (echo reference) are left out.
 *  - Android opens a third graph (hardware endpoints) that is not modelled.
 *
 * The only caller is the "Voice Call" kcontrol in q6apm-dai.c. It refuses to
 * run unless the voice_experimental module parameter is set (writable at
 * runtime), because it sends GRAPH_OPEN and friends to the ADSP.
 */
#include <linux/device.h>
#include <linux/dma-mapping.h>
#include <linux/idr.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/sizes.h>
#include <linux/slab.h>
#include <sound/pcm.h>
#include "audioreach.h"
#include "q6apm.h"

struct q6voice_sg {
	u32 id, perf, dir, scenario;
};

struct q6voice_cont {
	u32 id, cap, graph_pos, stack, domain, sg_idx;
};

struct q6voice_mod {
	u32 mid, miid, ip, op, cont_idx;
};

struct q6voice_conn {
	u32 src_miid, src_port, dst_miid, dst_port;
};

/* VCPM role of a module in a sub-graph (ACDB 0x08001162 / 0x080011b2) */
struct q6voice_x162 {
	u32 sg, vcpm_param, miid;
};

/* Module control link (ACDB 0x08001061) */
struct q6voice_ctrl {
	u32 sg, src, src_port, dst, dst_port, intent;
};

#include "q6voice-graphs.h"
#include "q6voice-ctrl.h"

/*
 * Cross-sub-graph TX control links that Android sends in its own GRAPH_OPEN
 * records; they are not in the per-SG ACDB data. sg 0 = separate record.
 */
static const struct q6voice_ctrl q6voice_tx_ctrl_xsg[] = {
	{ 0, 0x4448, 0xc0000001, 0x43af, 0x80000004, 0x080010c2 },
	{ 0, 0x4448, 0xc0000003, 0x41b0, 0x80000004, 0x080010c2 },
	{ 0, 0x43af, 0x80000009, 0x41b0, 0x80000005, 0x08001089 },
};

#define Q6VOICE_PARAM_CTRL_LINKS	0x08001061
#define Q6VOICE_PARAM_VCPM_SG_MODS	0x08001162
#define Q6VOICE_PROP_VCPM_MOD_MAP	0x080011b2
#define Q6VOICE_PROP_CTRL_INTENT	0x08001062
#define Q6VOICE_PROP_CTRL_136F		0x0800136f	/* always { 1 } in ACDB */

struct q6voice_ctrl_obj {
	u32 src, src_port, dst, dst_port, num_props;
	u32 intent_id, intent_size, intent_num, intent;
	u32 p2_id, p2_size, p2_val;
} __packed;
#include "q6voice-oob.h"	/* generated from an Android capture, not in git */

#define Q6VOICE_GRAPH_TX	0x7f10
#define Q6VOICE_GRAPH_RX	0x7f11

/* VCPM state values seen in the capture; the meaning is a guess. */
#define Q6VOICE_VCPM_STATE_PRE_START	2
#define Q6VOICE_VCPM_STATE_RUNNING	1

static bool experimental;
module_param_named(voice_experimental, experimental, bool, 0644);
MODULE_PARM_DESC(voice_experimental, "Allow q6apm_voice_start() to talk to the ADSP (unfinished)");

static bool replay;
module_param_named(voice_replay, replay, bool, 0644);
MODULE_PARM_DESC(voice_replay, "Before VSID, replay the out-of-band blobs captured from Android (experiment)");

#include "q6voice-agraph.h"	/* generated from an Android capture, not in git */

static unsigned int agraph;
module_param_named(voice_agraph, agraph, uint, 0644);
MODULE_PARM_DESC(voice_agraph, "GRAPH_OPEN payload: 0 our graphs, 1 Android capture as is, 2 Android capture without links to modules outside both graphs (experiment)");

struct q6voice_graph {
	bool registered;	/* info is in apm->graph_info_idr */
	struct audioreach_graph_info *info;
	struct audioreach_flat_conn *conns;
	struct q6apm_graph *graph;
	void *extra;		/* extra GRAPH_OPEN records, see q6voice_put_extra() */
};

static DEFINE_MUTEX(q6voice_lock);
static struct q6voice_graph q6voice_tx, q6voice_rx;
static u32 q6voice_cur_vsid;	/* 0: no call graphs open */

/* kcontrol selector -> VSID: Off, SIM1, SIM2 (fuxi) */
static const u32 q6voice_vsids[] = { 0, 0x11c05000, 0x11dc5000 };

/**
 * q6apm_voice_sel_to_vsid() - map a "Voice Call" selector to a VSID
 * @sel: 0 (Off), 1 (SIM1) or 2 (SIM2)
 *
 * Return: the VSID, or 0 for Off and for out-of-range selectors.
 */
u32 q6apm_voice_sel_to_vsid(unsigned int sel)
{
	return sel < ARRAY_SIZE(q6voice_vsids) ? q6voice_vsids[sel] : 0;
}
EXPORT_SYMBOL_GPL(q6apm_voice_sel_to_vsid);

/**
 * q6apm_voice_get_sel() - selector of the running call, 0 if none
 */
unsigned int q6apm_voice_get_sel(void)
{
	unsigned int i, sel = 0;

	mutex_lock(&q6voice_lock);
	for (i = 1; i < ARRAY_SIZE(q6voice_vsids); i++)
		if (q6voice_vsids[i] == q6voice_cur_vsid)
			sel = i;
	mutex_unlock(&q6voice_lock);

	return sel;
}
EXPORT_SYMBOL_GPL(q6apm_voice_get_sel);

static void q6voice_free_graph(struct q6apm *apm, struct q6voice_graph *vg)
{
	struct audioreach_sub_graph *sg, *sgn;
	struct audioreach_container *c, *cn;
	struct audioreach_module *m, *mn;

	if (!vg->info)
		return;

	if (vg->registered) {
		mutex_lock(&apm->lock);
		idr_remove(&apm->graph_info_idr, vg->info->id);
		mutex_unlock(&apm->lock);
	}

	list_for_each_entry_safe(sg, sgn, &vg->info->sg_list, node) {
		list_for_each_entry_safe(c, cn, &sg->container_list, node) {
			list_for_each_entry_safe(m, mn, &c->modules_list, node) {
				list_del(&m->node);
				kfree(m);
			}
			list_del(&c->node);
			kfree(c);
		}
		list_del(&sg->node);
		kfree(sg);
	}

	kfree(vg->conns);
	kfree(vg->extra);
	kfree(vg->info);
	memset(vg, 0, sizeof(*vg));
}

static void *q6voice_put_hdr(void *p, u32 miid, u32 param_id, u32 size)
{
	struct apm_module_param_data *d = p;

	d->module_instance_id = miid;
	d->param_id = param_id;
	d->param_size = size;
	d->error_code = 0;
	return d + 1;
}

/*
 * Control links (0x08001061, to APM) and VCPM module roles (0x08001162, to
 * VCPM), one record per run of rows with the same sg, laid out as in the
 * Android GRAPH_OPEN capture. With buf == NULL only the size is computed;
 * buf must be zeroed (record padding). Returns the size in bytes.
 */
static size_t q6voice_put_extra(void *buf, const struct q6voice_ctrl *ctrl, int nctrl,
				const struct q6voice_x162 *x, int nx)
{
	size_t off = 0;
	int i, j, k;

	for (i = 0; i < nctrl; i = j) {
		u32 sz;

		j = i + 1;
		while (ctrl[i].sg && j < nctrl && ctrl[j].sg == ctrl[i].sg)
			j++;
		sz = sizeof(u32) + (j - i) * sizeof(struct q6voice_ctrl_obj);
		if (buf) {
			u32 *n = q6voice_put_hdr(buf + off, APM_MODULE_INSTANCE_ID,
						 Q6VOICE_PARAM_CTRL_LINKS, sz);
			struct q6voice_ctrl_obj *o = (void *)(n + 1);

			*n = j - i;
			for (k = i; k < j; k++, o++) {
				o->src = ctrl[k].src;
				o->src_port = ctrl[k].src_port;
				o->dst = ctrl[k].dst;
				o->dst_port = ctrl[k].dst_port;
				o->num_props = 2;
				o->intent_id = Q6VOICE_PROP_CTRL_INTENT;
				o->intent_size = 2 * sizeof(u32);
				o->intent_num = 1;
				o->intent = ctrl[k].intent;
				o->p2_id = Q6VOICE_PROP_CTRL_136F;
				o->p2_size = sizeof(u32);
				o->p2_val = 1;
			}
		}
		off += APM_MODULE_PARAM_DATA_SIZE + ALIGN(sz, 8);
	}

	for (i = 0; i < nx; i = j) {
		u32 cnt, sz;

		for (j = i + 1; j < nx && x[j].sg == x[i].sg; j++)
			;
		cnt = j - i;
		/* { n=1, sg, nprops=1, prop_id, prop_size, cnt, cnt * { param, miid } } */
		sz = (6 + 2 * cnt) * sizeof(u32);
		if (buf) {
			u32 *w = q6voice_put_hdr(buf + off, VCPM_MODULE_INSTANCE_ID,
						 Q6VOICE_PARAM_VCPM_SG_MODS, sz);

			*w++ = 1;
			*w++ = x[i].sg;
			*w++ = 1;
			*w++ = Q6VOICE_PROP_VCPM_MOD_MAP;
			*w++ = (1 + 2 * cnt) * sizeof(u32);
			*w++ = cnt;
			for (k = i; k < j; k++) {
				*w++ = x[k].vcpm_param;
				*w++ = x[k].miid;
			}
		}
		off += APM_MODULE_PARAM_DATA_SIZE + ALIGN(sz, 8);
	}

	return off;
}

static int q6voice_build_extra(struct q6voice_graph *vg,
			       const struct q6voice_ctrl *ctrl, int nctrl,
			       const struct q6voice_ctrl *xsg, int nxsg,
			       const struct q6voice_x162 *x, int nx)
{
	size_t s1 = q6voice_put_extra(NULL, ctrl, nctrl, NULL, 0);
	size_t s2 = q6voice_put_extra(NULL, xsg, nxsg, NULL, 0);
	size_t s3 = q6voice_put_extra(NULL, NULL, 0, x, nx);

	vg->extra = kzalloc(s1 + s2 + s3, GFP_KERNEL);
	if (!vg->extra)
		return -ENOMEM;
	q6voice_put_extra(vg->extra, ctrl, nctrl, NULL, 0);
	q6voice_put_extra(vg->extra + s1, xsg, nxsg, NULL, 0);
	q6voice_put_extra(vg->extra + s1 + s2, NULL, 0, x, nx);
	vg->info->extra_params = vg->extra;
	vg->info->extra_params_size = s1 + s2 + s3;
	return 0;
}

static int q6voice_build_graph(struct q6apm *apm, struct q6voice_graph *vg, u32 graph_id,
			       const struct q6voice_sg *sgs, int nsg,
			       const struct q6voice_cont *conts, int ncont,
			       const struct q6voice_mod *mods, int nmod,
			       const struct q6voice_conn *conns, int nconn,
			       const struct q6voice_ctrl *ctrl, int nctrl,
			       const struct q6voice_ctrl *xsg, int nxsg,
			       const struct q6voice_x162 *x162, int nx162)
{
	struct audioreach_sub_graph **sgp __free(kfree) = NULL;
	struct audioreach_container **cp __free(kfree) = NULL;
	struct audioreach_graph_info *info;
	int i, ret;

	sgp = kcalloc(nsg, sizeof(*sgp), GFP_KERNEL);
	cp = kcalloc(ncont, sizeof(*cp), GFP_KERNEL);
	info = kzalloc(sizeof(*info), GFP_KERNEL);
	vg->conns = kcalloc(nconn, sizeof(*vg->conns), GFP_KERNEL);
	if (!sgp || !cp || !info || !vg->conns) {
		kfree(info);
		kfree(vg->conns);
		vg->conns = NULL;
		return -ENOMEM;
	}

	INIT_LIST_HEAD(&info->sg_list);
	vg->info = info;

	for (i = 0; i < nsg; i++) {
		struct audioreach_sub_graph *sg = kzalloc(sizeof(*sg), GFP_KERNEL);

		if (!sg) {
			ret = -ENOMEM;
			goto err;
		}
		sg->sub_graph_id = sgs[i].id;
		sg->perf_mode = sgs[i].perf;
		sg->direction = sgs[i].dir;
		sg->scenario_id = sgs[i].scenario;
		INIT_LIST_HEAD(&sg->container_list);
		sg->info = info;
		list_add_tail(&sg->node, &info->sg_list);
		info->num_sub_graphs++;
		sgp[i] = sg;
	}

	for (i = 0; i < ncont; i++) {
		struct audioreach_container *c = kzalloc(sizeof(*c), GFP_KERNEL);

		if (!c) {
			ret = -ENOMEM;
			goto err;
		}
		c->container_id = conts[i].id;
		c->capability_id = conts[i].cap;
		c->graph_pos = conts[i].graph_pos;
		c->stack_size = conts[i].stack;
		c->proc_domain = conts[i].domain;
		INIT_LIST_HEAD(&c->modules_list);
		c->sub_graph = sgp[conts[i].sg_idx];
		list_add_tail(&c->node, &c->sub_graph->container_list);
		c->sub_graph->num_containers++;
		cp[i] = c;
	}

	for (i = 0; i < nmod; i++) {
		struct audioreach_module *m;

		/*
		 * max_op_port may exceed AR_MAX_MOD_LINKS (0x7001011 has up to
		 * 29); populate_graph() caps its per-module link loop and our
		 * links go through flat_conns.
		 */
		m = kzalloc(sizeof(*m), GFP_KERNEL);
		if (!m) {
			ret = -ENOMEM;
			goto err;
		}
		m->module_id = mods[i].mid;
		m->instance_id = mods[i].miid;
		m->max_ip_port = mods[i].ip;
		m->max_op_port = mods[i].op;
		m->container = cp[mods[i].cont_idx];
		list_add_tail(&m->node, &m->container->modules_list);
		m->container->num_modules++;
	}

	for (i = 0; i < nconn; i++) {
		vg->conns[i].src_mod_inst_id = conns[i].src_miid;
		vg->conns[i].src_mod_op_port_id = conns[i].src_port;
		vg->conns[i].dst_mod_inst_id = conns[i].dst_miid;
		vg->conns[i].dst_mod_ip_port_id = conns[i].dst_port;
	}
	info->flat_conns = vg->conns;
	info->num_flat_conns = nconn;

	ret = q6voice_build_extra(vg, ctrl, nctrl, xsg, nxsg, x162, nx162);
	if (ret)
		goto err;

	mutex_lock(&apm->lock);
	ret = idr_alloc_u32(&apm->graph_info_idr, info, &graph_id, graph_id, GFP_KERNEL);
	mutex_unlock(&apm->lock);
	if (ret < 0)
		goto err;

	info->id = graph_id;
	vg->registered = true;
	return 0;
err:
	q6voice_free_graph(apm, vg);
	return ret;
}

/*
 * Experiment: open a graph with a GRAPH_OPEN payload captured from Android.
 * Only the sub-graph ids are kept on our side, for PREPARE/START/CLOSE; there
 * are no containers or modules, so nothing can look up module iids.
 */
static int q6voice_build_raw(struct q6apm *apm, struct q6voice_graph *vg, u32 graph_id,
			     const void *open, u32 open_size, const u32 *sgs, int nsg)
{
	struct audioreach_graph_info *info;
	int i, ret;

	info = kzalloc(sizeof(*info), GFP_KERNEL);
	if (!info)
		return -ENOMEM;
	INIT_LIST_HEAD(&info->sg_list);
	info->raw_open = open;
	info->raw_open_size = open_size;
	vg->info = info;

	for (i = 0; i < nsg; i++) {
		struct audioreach_sub_graph *sg = kzalloc(sizeof(*sg), GFP_KERNEL);

		if (!sg) {
			ret = -ENOMEM;
			goto err;
		}
		sg->sub_graph_id = sgs[i];
		INIT_LIST_HEAD(&sg->container_list);
		sg->info = info;
		list_add_tail(&sg->node, &info->sg_list);
		info->num_sub_graphs++;
	}

	mutex_lock(&apm->lock);
	ret = idr_alloc_u32(&apm->graph_info_idr, info, &graph_id, graph_id, GFP_KERNEL);
	mutex_unlock(&apm->lock);
	if (ret < 0)
		goto err;

	info->id = graph_id;
	vg->registered = true;
	return 0;
err:
	q6voice_free_graph(apm, vg);
	return ret;
}

static int q6voice_build_graphs(struct device *dev, struct q6apm *apm, unsigned int mode)
{
	int ret;

	if (mode == 1 || mode == 2) {
		bool s = mode == 2;

		dev_info(dev, "voice: GRAPH_OPEN from Android capture (%s)\n",
			 s ? "stripped" : "as captured");
		ret = q6voice_build_raw(apm, &q6voice_tx, Q6VOICE_GRAPH_TX,
					s ? q6voice_agraph_tx_strip : q6voice_agraph_tx,
					s ? sizeof(q6voice_agraph_tx_strip) : sizeof(q6voice_agraph_tx),
					q6voice_agraph_tx_sgs, ARRAY_SIZE(q6voice_agraph_tx_sgs));
		if (ret)
			return ret;
		ret = q6voice_build_raw(apm, &q6voice_rx, Q6VOICE_GRAPH_RX,
					s ? q6voice_agraph_rx_strip : q6voice_agraph_rx,
					s ? sizeof(q6voice_agraph_rx_strip) : sizeof(q6voice_agraph_rx),
					q6voice_agraph_rx_sgs, ARRAY_SIZE(q6voice_agraph_rx_sgs));
		if (ret)
			q6voice_free_graph(apm, &q6voice_tx);
		return ret;
	}

	ret = q6voice_build_graph(apm, &q6voice_tx, Q6VOICE_GRAPH_TX,
				  q6voice_tx_sgs, ARRAY_SIZE(q6voice_tx_sgs),
				  q6voice_tx_conts, ARRAY_SIZE(q6voice_tx_conts),
				  q6voice_tx_mods, ARRAY_SIZE(q6voice_tx_mods),
				  q6voice_tx_conns, ARRAY_SIZE(q6voice_tx_conns),
				  q6voice_tx_ctrl, ARRAY_SIZE(q6voice_tx_ctrl),
				  q6voice_tx_ctrl_xsg, ARRAY_SIZE(q6voice_tx_ctrl_xsg),
				  q6voice_tx_x162, ARRAY_SIZE(q6voice_tx_x162));
	if (ret)
		return ret;
	ret = q6voice_build_graph(apm, &q6voice_rx, Q6VOICE_GRAPH_RX,
				  q6voice_rx_sgs, ARRAY_SIZE(q6voice_rx_sgs),
				  q6voice_rx_conts, ARRAY_SIZE(q6voice_rx_conts),
				  q6voice_rx_mods, ARRAY_SIZE(q6voice_rx_mods),
				  q6voice_rx_conns, ARRAY_SIZE(q6voice_rx_conns),
				  q6voice_rx_ctrl, ARRAY_SIZE(q6voice_rx_ctrl),
				  NULL, 0,
				  q6voice_rx_x162, ARRAY_SIZE(q6voice_rx_x162));
	if (ret)
		q6voice_free_graph(apm, &q6voice_tx);
	return ret;
}

static int q6voice_set_state(struct q6apm *apm, u32 vsid, u32 state)
{
	struct param_id_vcpm_state p = { .vsid = vsid, .state = state };

	return audioreach_vcpm_set_param(apm, PARAM_ID_VCPM_STATE, &p, sizeof(p));
}

static int q6voice_set_path(struct q6apm *apm, u32 vsid, u32 first_value)
{
	struct param_id_vcpm_path_cfg p = {
		.vsid = vsid,
		.num_pairs = 3,
		.pairs = {
			{ 0x08000166, first_value },
			{ 0x080011b6, 0 },
			{ 0x08000167, 0 },
		},
	};

	return audioreach_vcpm_set_param(apm, PARAM_ID_VCPM_PATH_CFG, &p, sizeof(p));
}

/*
 * Replay of the out-of-band blobs the vendor HAL sends before VSID.  In the
 * capture, userspace passes {fd, offset} and the kernel driver turns that into
 * an address; here the address is the DMA address of our own buffer.
 * Every step only logs; a failure does not stop the sequence, so VSID is
 * still tried and we see how far the ADSP gets.
 */
#define Q6VOICE_OOB_BUF_SIZE	SZ_64K

struct q6voice_oob_blob {
	const char *name;
	const u8 *data;
	u32 size;
	u32 off;
	u32 opcode;
	bool tx;
};

static const struct q6voice_oob_blob q6voice_oob_blobs[] = {
	{ "rx_cal1", q6voice_oob_rx_cal1, sizeof(q6voice_oob_rx_cal1), 0x1000, APM_CMD_UNK_1008, false },
	{ "rx_cal2", q6voice_oob_rx_cal2, sizeof(q6voice_oob_rx_cal2), 0x2000, APM_CMD_UNK_1008, false },
	{ "tx_ep",   q6voice_oob_tx_ep,   sizeof(q6voice_oob_tx_ep),   0x3000, APM_CMD_SET_CFG,   true },
	{ "tx_cal1", q6voice_oob_tx_cal1, sizeof(q6voice_oob_tx_cal1), 0x4000, APM_CMD_UNK_1008, true },
	{ "tx_cal2", q6voice_oob_tx_cal2, sizeof(q6voice_oob_tx_cal2), 0x6000, APM_CMD_UNK_1008, true },
};

static void *q6voice_oob_cpu;
static dma_addr_t q6voice_oob_dma;

static int q6voice_oob_map(struct device *dev, struct q6apm *apm)
{
	struct apm_shared_map_region_payload *reg;
	struct apm_cmd_shared_mem_map_regions *cmd;
	struct gpr_pkt *pkt;
	int ret;

	pkt = audioreach_alloc_apm_cmd_pkt(sizeof(*cmd) + sizeof(*reg),
					   APM_CMD_SHARED_MEM_MAP_REGIONS, Q6VOICE_GRAPH_RX);
	if (IS_ERR(pkt))
		return PTR_ERR(pkt);

	/* Same raw layout as __q6apm_map_memory_fixed_region(); flag 4 as in the capture. */
	cmd = (void *)pkt + GPR_HDR_SIZE;
	cmd->mem_pool_id = 3;	/* APM_MEMORY_MAP_SHMEM8_4K_POOL */
	cmd->num_regions = 1;
	cmd->property_flag = 4;
	reg = (void *)cmd + sizeof(*cmd);
	reg->shm_addr_lsw = lower_32_bits(q6voice_oob_dma);
	reg->shm_addr_msw = upper_32_bits(q6voice_oob_dma);
	reg->mem_size_bytes = Q6VOICE_OOB_BUF_SIZE;

	ret = q6apm_send_cmd_sync(apm, pkt, APM_CMD_RSP_SHARED_MEM_MAP_REGIONS);
	kfree(pkt);
	return ret;
}

static int q6voice_oob_send(struct q6apm_graph *graph, u32 handle,
			    const struct q6voice_oob_blob *b)
{
	struct apm_cmd_header *h;
	struct gpr_pkt *pkt;
	dma_addr_t addr = q6voice_oob_dma + b->off;
	int ret;

	memcpy(q6voice_oob_cpu + b->off, b->data, b->size);

	pkt = audioreach_alloc_cmd_pkt(0, b->opcode, 0, graph->port->id, APM_MODULE_INSTANCE_ID);
	if (IS_ERR(pkt))
		return PTR_ERR(pkt);

	h = (void *)pkt + GPR_HDR_SIZE;
	h->payload_address_lsw = lower_32_bits(addr);
	h->payload_address_msw = upper_32_bits(addr);
	h->mem_map_handle = handle;
	h->payload_size = b->size;

	ret = audioreach_graph_send_cmd_sync(graph, pkt, 0);
	kfree(pkt);
	return ret;
}

static void q6voice_oob_replay(struct device *dev, struct q6apm *apm)
{
	u32 handle;
	int i, ret;

	q6voice_oob_cpu = dma_alloc_coherent(dev, Q6VOICE_OOB_BUF_SIZE, &q6voice_oob_dma, GFP_KERNEL);
	if (!q6voice_oob_cpu) {
		dev_err(dev, "voice replay: no memory\n");
		return;
	}
	memset(q6voice_oob_cpu, 0, Q6VOICE_OOB_BUF_SIZE);

	ret = q6voice_oob_map(dev, apm);
	handle = q6voice_rx.info->mem_map_handle;
	dev_info(dev, "voice replay: MAP %pad ret=%d handle=0x%x\n", &q6voice_oob_dma, ret, handle);
	if (ret || !handle)
		return;

	for (i = 0; i < ARRAY_SIZE(q6voice_oob_blobs); i++) {
		const struct q6voice_oob_blob *b = &q6voice_oob_blobs[i];

		ret = q6voice_oob_send(b->tx ? q6voice_tx.graph : q6voice_rx.graph, handle, b);
		dev_info(dev, "voice replay: %s op=0x%08x size=0x%x -> %d\n",
			 b->name, b->opcode, b->size, ret);
	}
}

static void q6voice_oob_release(struct device *dev)
{
	int ret;

	if (!q6voice_oob_cpu)
		return;
	if (q6voice_rx.info && q6voice_rx.info->mem_map_handle) {
		ret = q6apm_unmap_memory_fixed_region(dev, Q6VOICE_GRAPH_RX);
		dev_info(dev, "voice replay: UNMAP ret=%d\n", ret);
		if (ret) {
			/* The ADSP may still reference it: leak rather than free. */
			dev_warn(dev, "voice replay: UNMAP failed, keeping the buffer\n");
			return;
		}
	}
	dma_free_coherent(dev, Q6VOICE_OOB_BUF_SIZE, q6voice_oob_cpu, q6voice_oob_dma);
	q6voice_oob_cpu = NULL;
}

/**
 * q6apm_voice_start() - open, prepare and start the TX/RX voice graphs
 * @dev: a child device of the q6apm GPR device
 * @vsid: voice system id (SIM 2 on fuxi: 0x11dc5000, SIM 1: 0x11c05000)
 */
int q6apm_voice_start(struct device *dev, u32 vsid)
{
	struct q6apm *apm = dev_get_drvdata(dev->parent);
	struct param_id_vcpm_vsid p = { .vsid = vsid };
	const char *step;
	bool vcpm_set = false;
	int ret;

	if (!READ_ONCE(experimental))
		return -EOPNOTSUPP;
	if (!vsid)
		return -EINVAL;

	mutex_lock(&q6voice_lock);
	if (q6voice_cur_vsid) {
		ret = -EBUSY;
		goto unlock;
	}

	ret = q6voice_build_graphs(dev, apm, READ_ONCE(agraph));
	if (ret)
		goto unlock;

	/* graph_open() sends GRAPH_OPEN to the ADSP on first use of an id */

	q6voice_tx.graph = q6apm_graph_open(dev, NULL, NULL, Q6VOICE_GRAPH_TX,
					    SNDRV_PCM_STREAM_CAPTURE);
	if (IS_ERR(q6voice_tx.graph)) {
		ret = PTR_ERR(q6voice_tx.graph);
		q6voice_tx.graph = NULL;
		goto free;
	}
	q6voice_rx.graph = q6apm_graph_open(dev, NULL, NULL, Q6VOICE_GRAPH_RX,
					    SNDRV_PCM_STREAM_PLAYBACK);
	if (IS_ERR(q6voice_rx.graph)) {
		ret = PTR_ERR(q6voice_rx.graph);
		q6voice_rx.graph = NULL;
		goto close_tx;
	}

	/* TODO: CODEC_DMA endpoint HW_EP_MF_CFG / INTF_CFG are not sent here. */

	if (READ_ONCE(replay))
		q6voice_oob_replay(dev, apm);

	/* Order of the next steps is a guess, see the file header. */
	step = "VSID";
	ret = audioreach_vcpm_set_param(apm, PARAM_ID_VCPM_VSID, &p, sizeof(p));
	if (!ret) {
		vcpm_set = true;
		step = "STATE(PRE_START)";
		ret = q6voice_set_state(apm, vsid, Q6VOICE_VCPM_STATE_PRE_START);
	}
	if (!ret) {
		step = "PATH(3)";
		ret = q6voice_set_path(apm, vsid, 3);
	}
	if (!ret) {
		step = "prepare(TX)";
		ret = q6apm_graph_prepare(q6voice_tx.graph);
	}
	if (!ret) {
		step = "prepare(RX)";
		ret = q6apm_graph_prepare(q6voice_rx.graph);
	}
	if (!ret) {
		step = "start(TX)";
		ret = q6apm_graph_start(q6voice_tx.graph);
	}
	if (!ret) {
		step = "start(RX)";
		ret = q6apm_graph_start(q6voice_rx.graph);
	}
	if (!ret) {
		step = "STATE(RUNNING)";
		ret = q6voice_set_state(apm, vsid, Q6VOICE_VCPM_STATE_RUNNING);
	}
	if (!ret) {
		step = "PATH(1)";
		ret = q6voice_set_path(apm, vsid, 1);
	}
	if (ret) {
		dev_err(dev, "voice start failed at step %s: %d\n", step, ret);
		goto stop;
	}

	q6voice_cur_vsid = vsid;
	mutex_unlock(&q6voice_lock);
	return 0;

stop:
	if (vcpm_set) {
		/*
		 * VCPM already holds this VSID. The vendor HAL never sends a
		 * VCPM "off" state; its hang-up is GRAPH_STOP (RX, then TX)
		 * followed by GRAPH_CLOSE. Skipping STOP here (start_count is
		 * 0 when START failed) was followed by a modem VS fatal error
		 * (vs.c:1336, #232).
		 */
		int r1 = q6apm_graph_force_stop(q6voice_rx.graph);
		int r2 = q6apm_graph_force_stop(q6voice_tx.graph);

		dev_info(dev, "voice: error path GRAPH_STOP rx=%d tx=%d\n", r1, r2);
	} else {
		q6apm_graph_stop(q6voice_rx.graph);
		q6apm_graph_stop(q6voice_tx.graph);
	}
	q6voice_oob_release(dev);
	q6apm_graph_close(q6voice_rx.graph);
	q6voice_rx.graph = NULL;
close_tx:
	q6apm_graph_close(q6voice_tx.graph);
	q6voice_tx.graph = NULL;
free:
	q6voice_free_graph(apm, &q6voice_rx);
	q6voice_free_graph(apm, &q6voice_tx);
unlock:
	mutex_unlock(&q6voice_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(q6apm_voice_start);

int q6apm_voice_stop(struct device *dev)
{
	struct q6apm *apm = dev_get_drvdata(dev->parent);

	mutex_lock(&q6voice_lock);
	if (!q6voice_cur_vsid) {
		mutex_unlock(&q6voice_lock);
		return 0;
	}

	q6apm_graph_stop(q6voice_rx.graph);
	q6apm_graph_stop(q6voice_tx.graph);
	q6voice_oob_release(dev);
	q6apm_graph_close(q6voice_rx.graph);
	q6apm_graph_close(q6voice_tx.graph);
	q6voice_rx.graph = NULL;
	q6voice_tx.graph = NULL;
	q6voice_free_graph(apm, &q6voice_rx);
	q6voice_free_graph(apm, &q6voice_tx);
	q6voice_cur_vsid = 0;
	mutex_unlock(&q6voice_lock);

	return 0;
}
EXPORT_SYMBOL_GPL(q6apm_voice_stop);
