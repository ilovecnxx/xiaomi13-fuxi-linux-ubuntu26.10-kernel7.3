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
 *  - VCPM params are sent with the voice graph's GPR port as source (RX for
 *    VSID/PATH, TX for STATE), as in the capture; untested on hardware.
 *
 * The only caller is the "Voice Call" kcontrol in q6apm-dai.c. It refuses to
 * run unless the voice_experimental module parameter is set (writable at
 * runtime), because it sends GRAPH_OPEN and friends to the ADSP.
 */
#include <linux/device.h>
#include <linux/idr.h>
#include <linux/module.h>
#include <linux/mutex.h>
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

#include "q6voice-graphs.h"

#define Q6VOICE_GRAPH_TX	0x7f10
#define Q6VOICE_GRAPH_RX	0x7f11

/* VCPM state values seen in the capture; the meaning is a guess. */
#define Q6VOICE_VCPM_STATE_PRE_START	2
#define Q6VOICE_VCPM_STATE_RUNNING	1

static bool experimental;
module_param_named(voice_experimental, experimental, bool, 0644);
MODULE_PARM_DESC(voice_experimental, "Allow q6apm_voice_start() to talk to the ADSP (unfinished)");

struct q6voice_graph {
	bool registered;	/* info is in apm->graph_info_idr */
	struct audioreach_graph_info *info;
	struct audioreach_flat_conn *conns;
	struct q6apm_graph *graph;
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
	kfree(vg->info);
	memset(vg, 0, sizeof(*vg));
}

static int q6voice_build_graph(struct q6apm *apm, struct q6voice_graph *vg, u32 graph_id,
			       const struct q6voice_sg *sgs, int nsg,
			       const struct q6voice_cont *conts, int ncont,
			       const struct q6voice_mod *mods, int nmod,
			       const struct q6voice_conn *conns, int nconn)
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

static int q6voice_set_state(struct q6apm *apm, struct q6apm_graph *graph,
			     u32 vsid, u32 state)
{
	struct param_id_vcpm_state p = { .vsid = vsid, .state = state };

	return audioreach_vcpm_set_param(apm, graph, PARAM_ID_VCPM_STATE, &p, sizeof(p));
}

static int q6voice_set_path(struct q6apm *apm, struct q6apm_graph *graph,
			    u32 vsid, u32 first_value)
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

	return audioreach_vcpm_set_param(apm, graph, PARAM_ID_VCPM_PATH_CFG, &p, sizeof(p));
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

	ret = q6voice_build_graph(apm, &q6voice_tx, Q6VOICE_GRAPH_TX,
				  q6voice_tx_sgs, ARRAY_SIZE(q6voice_tx_sgs),
				  q6voice_tx_conts, ARRAY_SIZE(q6voice_tx_conts),
				  q6voice_tx_mods, ARRAY_SIZE(q6voice_tx_mods),
				  q6voice_tx_conns, ARRAY_SIZE(q6voice_tx_conns));
	if (ret)
		goto unlock;
	ret = q6voice_build_graph(apm, &q6voice_rx, Q6VOICE_GRAPH_RX,
				  q6voice_rx_sgs, ARRAY_SIZE(q6voice_rx_sgs),
				  q6voice_rx_conts, ARRAY_SIZE(q6voice_rx_conts),
				  q6voice_rx_mods, ARRAY_SIZE(q6voice_rx_mods),
				  q6voice_rx_conns, ARRAY_SIZE(q6voice_rx_conns));
	if (ret)
		goto free_tx;

	/* graph_open() sends GRAPH_OPEN to the ADSP on first use of an id */
	q6voice_tx.graph = q6apm_graph_open(dev, NULL, NULL, Q6VOICE_GRAPH_TX,
					    SNDRV_PCM_STREAM_CAPTURE);
	if (IS_ERR(q6voice_tx.graph)) {
		ret = PTR_ERR(q6voice_tx.graph);
		q6voice_tx.graph = NULL;
		goto free_rx;
	}
	q6voice_rx.graph = q6apm_graph_open(dev, NULL, NULL, Q6VOICE_GRAPH_RX,
					    SNDRV_PCM_STREAM_PLAYBACK);
	if (IS_ERR(q6voice_rx.graph)) {
		ret = PTR_ERR(q6voice_rx.graph);
		q6voice_rx.graph = NULL;
		goto close_tx;
	}

	/* TODO: CODEC_DMA endpoint HW_EP_MF_CFG / INTF_CFG are not sent here. */

	/* Order of the next steps is a guess, see the file header. */
	ret = audioreach_vcpm_set_param(apm, q6voice_rx.graph, PARAM_ID_VCPM_VSID, &p, sizeof(p));
	if (!ret)
		ret = q6voice_set_state(apm, q6voice_tx.graph, vsid, Q6VOICE_VCPM_STATE_PRE_START);
	if (!ret)
		ret = q6voice_set_path(apm, q6voice_rx.graph, vsid, 3);
	if (!ret)
		ret = q6apm_graph_prepare(q6voice_tx.graph);
	if (!ret)
		ret = q6apm_graph_prepare(q6voice_rx.graph);
	if (!ret)
		ret = q6apm_graph_start(q6voice_tx.graph);
	if (!ret)
		ret = q6apm_graph_start(q6voice_rx.graph);
	if (!ret)
		ret = q6voice_set_state(apm, q6voice_tx.graph, vsid, Q6VOICE_VCPM_STATE_RUNNING);
	if (!ret)
		ret = q6voice_set_path(apm, q6voice_rx.graph, vsid, 1);
	if (ret)
		goto stop;

	q6voice_cur_vsid = vsid;
	mutex_unlock(&q6voice_lock);
	return 0;

stop:
	q6apm_graph_stop(q6voice_rx.graph);
	q6apm_graph_stop(q6voice_tx.graph);
	q6apm_graph_close(q6voice_rx.graph);
	q6voice_rx.graph = NULL;
close_tx:
	q6apm_graph_close(q6voice_tx.graph);
	q6voice_tx.graph = NULL;
free_rx:
	q6voice_free_graph(apm, &q6voice_rx);
free_tx:
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
