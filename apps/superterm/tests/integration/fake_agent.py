#!/usr/bin/env python3
"""Fake codex-like agent: emits JSONL records slowly to exercise the streaming driver.

Usage: fake_agent.py <mode>   mode ∈ normal | slow | badjson | fail | hang
"""
import json
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    emit({"type": "thread.started", "thread_id": "fake-t"})
    emit({"type": "turn.started"})
    emit({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "working"}})
    if mode == "slow":
        for i in range(3):
            time.sleep(0.3)
            emit({"type": "item.completed", "item": {"id": f"m{i}", "type": "agent_message", "text": f"chunk {i}"}})
    if mode == "badjson":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
    if mode == "hang":
        emit({"type": "item.started", "item": {"id": "c0", "type": "command_execution",
              "command": "sleep 100", "aggregated_output": "", "exit_code": None, "status": "in_progress"}})
        time.sleep(100)
        return
    emit({"type": "item.started", "item": {"id": "c1", "type": "command_execution",
          "command": "echo hi", "aggregated_output": "", "exit_code": None, "status": "in_progress"}})
    emit({"type": "item.completed", "item": {"id": "c1", "type": "command_execution",
          "command": "echo hi", "aggregated_output": "hi\n", "exit_code": 0, "status": "completed"}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}})
    if mode == "fail":
        sys.exit(3)


if __name__ == "__main__":
    main()
