#!/usr/bin/python3
from fuxi_voice_calls import run_collector


if __name__ == '__main__':
    try:
        raise SystemExit(run_collector())
    except Exception:
        raise SystemExit('VOICE collector unavailable') from None
