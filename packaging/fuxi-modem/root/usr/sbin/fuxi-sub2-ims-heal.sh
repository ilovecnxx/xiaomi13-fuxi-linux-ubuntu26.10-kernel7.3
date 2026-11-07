#!/bin/bash
# fuxi-sub2-ims-heal: 开机后若卡 2 IMS 卡住，按 stop 81voltd -> restart MM -> start 81voltd 顺序重启一次。
# 背景：冷启动时卡 2 偶发长期 registering/not-registered；手动按此顺序重启约 30 s 恢复（TASKS.md 18:54）。
# 只尝试一次，不循环。FUXI_HEAL_DRYRUN=1 只打印不执行；FUXI_HEAL_FORCE=1 跳过"已注册则退出"（配合 DRYRUN 测试）。
# 日志走 stdout（journal），不含号码/IMSI。

IMS=${FUXI_IMS_TOOL:-/usr/sbin/fuxi-qmi-ims.py}
WAIT_SUB0=${FUXI_HEAL_WAIT_SUB0:-240}     # 等卡 1 registered 的上限（秒）
GRACE_SUB1=${FUXI_HEAL_GRACE_SUB1:-240}   # 卡 1 registered 后给卡 2 的宽限（秒）。开机约 160 s 前基带只给 ctnet，90 s 会误触发
RETRY_QUIET=${FUXI_HEAL_RETRY_QUIET:-180} # 81voltd 最近这么多秒内还在重试，就继续等（v5 退避最长 120 s）
EXTRA_MAX=${FUXI_HEAL_EXTRA_MAX:-300}     # 宽限结束后因 81voltd 在重试而追加等待的上限（秒）
VERIFY=${FUXI_HEAL_VERIFY:-120}           # 重启后观察上限（秒）
POLL=10

log() { echo "heal: $*"; }

status() {  # $1 = 订阅序号 0/1；输出 registered / registering / not-registered / unknown
    local s
    s=$(timeout 20 python3 "$IMS" "$1" 2>/dev/null | awk '/registration_status/ {print $2; exit}')
    echo "${s:-unknown}"
}

run() {
    if [ "${FUXI_HEAL_DRYRUN:-0}" = 1 ]; then log "DRYRUN: $*"; else "$@"; fi
}

# 1. 等卡 1 registered（卡 1 都没起来说明基带还没就绪，此时重启没有意义）
t=0
while [ "$t" -lt "$WAIT_SUB0" ]; do
    [ "$(status 0)" = registered ] && break
    sleep "$POLL"; t=$((t + POLL))
done
if [ "$(status 0)" != registered ]; then
    log "sub0 在 ${WAIT_SUB0}s 内未 registered，不动手（可能是网络/信号问题，不是卡 2 的启动竞争）"
    exit 0
fi
log "sub0 registered（等待 ${t}s）"

# 2. 给卡 2 宽限期
t=0
while [ "$t" -lt "$GRACE_SUB1" ]; do
    if [ "$(status 1)" = registered ] && [ "${FUXI_HEAL_FORCE:-0}" != 1 ]; then
        log "sub1 已 registered（宽限内 ${t}s），无需处理"
        exit 0
    fi
    sleep "$POLL"; t=$((t + POLL))
done

# 81voltd 自己还在退避重试时不打断它：顺序重启会让重试从头开始（2102 交接 §7.2）
retrying() {
    journalctl -u 81voltd --since="-${RETRY_QUIET}s" -q --no-pager 2>/dev/null \
        | grep -qE 'retrying start network|wrong PDN'
}
e=0
while [ "$e" -lt "$EXTRA_MAX" ] && [ "$(status 1)" != registered ] && retrying; do
    [ "$e" = 0 ] && log "81voltd 仍在重试卡 2，继续等（最多 ${EXTRA_MAX}s）"
    sleep "$POLL"; e=$((e + POLL))
done

if [ "$(status 1)" = registered ] && [ "${FUXI_HEAL_FORCE:-0}" != 1 ]; then
    log "sub1 已 registered（宽限 ${GRACE_SUB1}s + 追加 ${e}s 内），无需处理"
    exit 0
fi
log "sub1=$(status 1)，宽限 ${GRACE_SUB1}s + 追加 ${e}s 后仍未 registered，开始顺序重启（仅一次）"

# 3. 顺序重启：不能单独重启 81voltd，必须先 stop 81voltd、再 restart MM、再 start 81voltd
run systemctl stop 81voltd
run systemctl restart ModemManager
[ "${FUXI_HEAL_DRYRUN:-0}" = 1 ] || sleep 1
run systemctl start 81voltd

# 4. 观察结果
[ "${FUXI_HEAL_DRYRUN:-0}" = 1 ] && { log "DRYRUN 结束"; exit 0; }
t=0
while [ "$t" -lt "$VERIFY" ]; do
    sleep "$POLL"; t=$((t + POLL))
    a=$(status 0); b=$(status 1)
    if [ "$a" = registered ] && [ "$b" = registered ]; then
        log "恢复成功：sub0/sub1 都 registered（重启后 ${t}s）"
        exit 0
    fi
done
log "重启后 ${VERIFY}s：sub0=$(status 0) sub1=$(status 1)，未恢复。不再重试，请人工查看（TASKS.md 回滚/排查）"
exit 1
