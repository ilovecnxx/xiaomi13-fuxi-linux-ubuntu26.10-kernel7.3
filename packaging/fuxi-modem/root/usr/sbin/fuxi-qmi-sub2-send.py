#!/usr/bin/python3
"""Usage: fuxi-qmi-sub2-send.py --json | [--dry-run] [--no-ims] NUMBER TEXT"""
import sys
import time

from fuxi_sub2_sms import build_pdus, run_send, submit_pdus


def main():
    args = sys.argv[1:]
    if args == ["--json"]:
        return run_send(sys.stdin.buffer)
    flags = {arg for arg in args if arg.startswith("--")}
    values = [arg for arg in args if not arg.startswith("--")]
    if len(values) != 2 or flags - {"--dry-run", "--no-ims"}:
        sys.exit(__doc__)
    try:
        pdus = build_pdus(*values)
    except ValueError:
        sys.exit("invalid number or SMS text")
    if len(pdus) > 1:
        print(f"segments_total={len(pdus)}", flush=True)
    for pdu in pdus:
        print(f"pdu_len={len(pdu)}", flush=True)
    if "--dry-run" in flags:
        return 0

    def log(message):
        print(f"{time.strftime('%T')} {message}", flush=True)

    result = submit_pdus(pdus, ims="--no-ims" not in flags, log=log)
    if len(pdus) > 1:
        log(f"segments_submitted={result.segments_submitted} segments_uncertain={result.segments_uncertain}")
    if result.cleanup_failed:
        log("cleanup failed")
    if result.status == "submitted":
        log(f"send OK, message_id={result.message_id}")
        return 0
    log(f"send {result.status}: {result.error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
