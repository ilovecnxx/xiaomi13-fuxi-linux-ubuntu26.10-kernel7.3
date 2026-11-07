#!/usr/bin/python3
# fuxi UIM 预配会话守护（ModemManager 启动前跑一次）。
# 11-04 起基带开机后不再自动激活 GW 预配会话，MM 报 sim-missing，数据/IMS 全断。
# 手动激活（卡槽 1 → primary-gw、卡槽 2 → secondary-gw）后 MM 正常。这里自动做：
#   等卡在位 → 等基带自行预配 SETTLE 秒 → 仍缺的会话按卡里 USIM 应用的 AID 激活。
# 只调 UIM change-provisioning-session，不写 NV、不碰 EFS。会话已在就什么都不做。
import os
import re
import subprocess
import sys
import time

DEV = os.environ.get("FUXI_QMI_DEV", "qrtr://0")
WAIT = int(os.environ.get("FUXI_UIM_WAIT", "60"))
SETTLE = int(os.environ.get("FUXI_UIM_SETTLE", "8"))
# 卡槽 → 会话
SESSIONS = {1: ("Primary GW", "primary-gw-provisioning"),
            2: ("Secondary GW", "secondary-gw-provisioning")}


def log(msg):
    print(msg, flush=True)


def card_status():
    try:
        out = subprocess.run(["qmicli", "-d", DEV, "--uim-get-card-status"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def parse(text):
    """返回 (sessions, slots)：sessions[name] = 是否存在；slots[n] = {present, usim_aid}"""
    sessions, slots = {}, {}
    slot = app = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r"(Primary GW|Secondary GW|Primary 1X|Secondary 1X):\s+(.*)", s)
        if m:
            sessions[m.group(1)] = "doesn't exist" not in m.group(2)
            continue
        m = re.match(r"Slot \[(\d+)\]:", s)
        if m:
            slot = int(m.group(1))
            slots[slot] = {"present": False, "usim_aid": None}
            app = None
            continue
        if slot is None:
            continue
        if s.startswith("Card state:"):
            slots[slot]["present"] = "'present'" in s
        elif s.startswith("Application type:"):
            app = "usim" if "'usim" in s else "other"
        elif s == "Application ID:" and app == "usim" and i + 1 < len(lines):
            aid = lines[i + 1].strip()
            if re.fullmatch(r"([0-9A-Fa-f]{2}:)+[0-9A-Fa-f]{2}", aid) and not slots[slot]["usim_aid"]:
                slots[slot]["usim_aid"] = aid
    return sessions, slots


def main():
    deadline = time.monotonic() + WAIT
    status = None
    while time.monotonic() < deadline:
        text = card_status()
        if text:
            _, slots = parse(text)
            if slots and any(v["present"] for v in slots.values()):
                status = text
                break
        time.sleep(2)
    if status is None:
        log(f"no card present within {WAIT}s, skip")
        return 0
    sessions, slots = parse(status)
    # Already-provisioned cards need no settling delay on the boot critical path.
    present_sessions = [sessions.get(name) for n, (name, _) in SESSIONS.items()
                        if slots.get(n, {}).get("present")]
    if not present_sessions or not all(value is True for value in present_sessions):
        time.sleep(SETTLE)
        sessions, slots = parse(card_status() or status)
    changed = 0
    for n, (name, stype) in SESSIONS.items():
        info = slots.get(n)
        if not info or not info["present"]:
            continue
        if sessions.get(name, True):
            log(f"slot {n}: {name} session present")
            continue
        if not info["usim_aid"]:
            log(f"slot {n}: {name} missing but no USIM AID found, skip")
            continue
        arg = f"session-type={stype},activate=yes,slot={n},aid={info['usim_aid']}"
        out = subprocess.run(["qmicli", "-d", DEV, f"--uim-change-provisioning-session={arg}"],
                             capture_output=True, text=True, timeout=30)
        msg = " ".join((out.stdout + out.stderr).split())
        log(f"slot {n}: activate {name}: rc={out.returncode} {msg}")
        changed += out.returncode == 0
    if changed:
        time.sleep(2)
        sessions, _ = parse(card_status() or "")
        log("after: " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in sessions.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
