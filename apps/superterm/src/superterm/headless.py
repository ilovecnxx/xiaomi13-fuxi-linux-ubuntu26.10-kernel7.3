"""Headless CLI for SuperTerm — exercises the full backend without a GUI.

Commands:
  doctor                         capability/prereq report
  providers                      list provider profiles (masked) + bindings
  import-cc [--db PATH]          import providers from a cc-switch DB (read-only)
  bind AGENT PROVIDER [MODEL]    bind an agent to a provider (+materialize config)
  apply AGENT                    materialize the active binding to the CLI config
  run PROVIDER "PROMPT" [--workspace DIR] [--model M] [--timeout S]
                                 one-shot turn, prints normalized events as JSONL
  sessions                       list sessions
  show SESSION_ID [--after N]    replay a session's events
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Optional

from .app import App
from . import paths


def _print_err(msg: str) -> None:
    sys.stderr.write(f"error: {msg}\n")


def cmd_doctor(app: App) -> int:
    import shutil
    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "state_dir": str(paths.state_dir()),
        "db_exists": os.path.exists(paths.db_path()),
    }
    for b in ("codex", "claude", "dsh"):
        report[b] = shutil.which(b) or "MISSING"
    try:
        import gi  # noqa: F401
        report["pygobject"] = "ok"
    except Exception:  # noqa: BLE001
        report["pygobject"] = "missing"
    print(json.dumps(report, indent=2))
    return 0


def cmd_providers(app: App) -> int:
    out = {"profiles": [], "bindings": {}}
    for p in app.config.list_profiles():
        out["profiles"].append(p.summary(app.config.secrets))
    for a in ("codex", "claude", "dsh"):
        b = app.config.get_binding(a)
        out["bindings"][a] = b
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_import_cc(app: App, db: Optional[str]) -> int:
    from .config import import_cc_switch
    dbp = db or os.path.expanduser("~/.cc-switch/cc-switch.db")
    prev = import_cc_switch.preview_import(dbp)
    print(json.dumps({"preview": prev}, ensure_ascii=False, indent=2))
    rep = import_cc_switch.do_import(dbp, app.config)
    print(json.dumps({"report": rep}, ensure_ascii=False, indent=2))
    return 0


def cmd_bind(app: App, agent: str, provider: str, model: Optional[str]) -> int:
    if not app.config.get_profile(provider):
        _print_err(f"no such provider {provider}")
        return 2
    app.config.set_binding(agent, provider, model)
    res = app.apply_binding(agent)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


def cmd_apply(app: App, agent: str) -> int:
    res = app.apply_binding(agent)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


def cmd_run(app: App, provider: str, prompt: str, workspace: str, model: Optional[str], timeout: Optional[float]) -> int:
    sess = app.create_session(provider, workspace, title=prompt[:40], model=model)

    async def go() -> int:
        async def push(msg):
            print(json.dumps(msg, ensure_ascii=False))

        app.controller.set_push(push)
        await app.start_turn(sess.id, prompt, model=model, timeout_s=timeout)
        # wait for the turn to finish
        while app.controller.is_running(sess.id):
            await asyncio.sleep(0.2)
        print(json.dumps({"session_id": sess.id, "done": True}))
        return 0

    return asyncio.run(go())


def cmd_sessions(app: App) -> int:
    rows = app.list_sessions()
    print(json.dumps([
        {"id": r.id, "provider": r.provider, "title": r.title, "workspace": r.workspace,
         "status": r.status, "created_at": r.created_at} for r in rows], ensure_ascii=False, indent=2))
    return 0


def cmd_show(app: App, sid: str, after: int) -> int:
    sess, events = app.open_session(sid, after)
    print(json.dumps({"session": {"id": sess.id, "provider": sess.provider, "workspace": sess.workspace},
                      "events": events}, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="superterm-headless")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor")
    sub.add_parser("providers")
    p_imp = sub.add_parser("import-cc"); p_imp.add_argument("--db")
    p_bind = sub.add_parser("bind")
    p_bind.add_argument("agent"); p_bind.add_argument("provider"); p_bind.add_argument("model", nargs="?")
    p_apply = sub.add_parser("apply"); p_apply.add_argument("agent")
    p_run = sub.add_parser("run")
    p_run.add_argument("provider"); p_run.add_argument("prompt")
    p_run.add_argument("--workspace", default=os.getcwd())
    p_run.add_argument("--model"); p_run.add_argument("--timeout", type=float)
    sub.add_parser("sessions")
    p_show = sub.add_parser("show"); p_show.add_argument("session_id"); p_show.add_argument("--after", type=int, default=0)

    args = parser.parse_args(argv)
    app = App()
    try:
        if args.cmd == "doctor":
            return cmd_doctor(app)
        if args.cmd == "providers":
            return cmd_providers(app)
        if args.cmd == "import-cc":
            return cmd_import_cc(app, args.db)
        if args.cmd == "bind":
            return cmd_bind(app, args.agent, args.provider, args.model)
        if args.cmd == "apply":
            return cmd_apply(app, args.agent)
        if args.cmd == "run":
            return cmd_run(app, args.provider, args.prompt, args.workspace, args.model, args.timeout)
        if args.cmd == "sessions":
            return cmd_sessions(app)
        if args.cmd == "show":
            return cmd_show(app, args.session_id, args.after)
        return 2
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
