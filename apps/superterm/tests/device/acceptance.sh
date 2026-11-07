#!/bin/bash
# SuperTerm on-device acceptance (read-only). Run as root on the phone:
#   bash /opt/superterm-acc/acceptance.sh [target_user]
# Prints PASS/FAIL lines and a final summary. Never mutates agent configs.
set -uo pipefail
U="${1:-fuxi}"
FUID="$(id -u "$U")"
H="$(getent passwd "$U" | cut -d: -f6)"
APP="$H/.local/share/superterm/current"
PASS=0; FAIL=0
ok(){ echo "PASS: $*"; PASS=$((PASS+1)); }
no(){ echo "FAIL: $*"; FAIL=$((FAIL+1)); }
run_u(){ sudo -u "$U" env HOME="$H" PYTHONPATH="$APP/src" "$@"; }

echo "== 1. install layout =="
[ -d "$APP" ] && ok "current symlink present: $(readlink -f "$APP")" || no "no current install"
[ -f "$H/.local/share/applications/superterm.desktop" ] && ok "desktop entry" || no "no desktop entry"
[ -f "$H/.local/share/icons/hicolor/scalable/apps/superterm.svg" ] && ok "icon" || no "no icon"
[ -x "$H/.local/bin/superterm-launch" ] && ok "launcher executable" || no "no launcher"

echo "== 2. prerequisites =="
[ "$(dpkg --print-architecture)" = arm64 ] && ok "arm64" || no "not arm64"
run_u python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); gi.require_version("WebKit","6.0")' \
  && ok "GTK4+Adw+WebKit6 python bindings" || no "GUI bindings missing"
for b in codex claude dsh; do
  if command -v "$b" >/dev/null; then ok "CLI $b ($("$b" --version 2>/dev/null | head -1))"; else no "CLI $b missing"; fi
done

echo "== 3. module import + doctor =="
run_u python3 -c 'import superterm.main as m; assert m._HAVE_GI; import superterm.bridge, superterm.app' \
  && ok "superterm modules import, HAVE_GI True" || no "module import failed"
run_u python3 -m superterm.headless doctor >/tmp/acc-doctor.json 2>/dev/null && ok "headless doctor runs" || no "doctor failed"

echo "== 4. backend unit/integration tests inside install =="
if [ -d "$APP/tests" ]; then
  ( cd "$APP" && run_u python3 -m unittest discover -s tests/unit >/tmp/acc-unit.log 2>&1 ) \
    && ok "embedded unit tests pass ($(grep -o 'Ran [0-9]* tests' /tmp/acc-unit.log))" || no "embedded unit tests failed"
else
  echo "SKIP: tests not shipped with install"
fi

echo "== 5. config subsystem (isolated temp state) =="
TMP=$(mktemp -d); chown "$U" "$TMP"
run_u env XDG_STATE_HOME="$TMP/s" XDG_CONFIG_HOME="$TMP/c" python3 - <<'PY' && ok "config store round-trip + token masking" || no "config subsystem"
import sys
from superterm.config.profiles import ConfigStore, Profile
s=ConfigStore()
s.upsert_profile(Profile(id="p",display_name="T",app_type="codex",base_url="https://x/v1",wire_api="responses"),token="sk-acctest123456789")
p=s.get_profile("p"); summ=p.summary(s.secrets)
assert summ["has_token"] and "sk-acctest" not in str(summ), summ
assert s.secrets.get("p")=="sk-acctest123456789"
print("ok")
PY
rm -rf "$TMP"

echo "== 6. drivers normalize contract (fake) =="
run_u python3 - <<'PY' && ok "codex/claude normalizers produce shared event model" || no "normalizers"
from superterm.drivers.normalize import CodexNormalizer, ClaudeNormalizer
from superterm import models as M
c=CodexNormalizer(); evs=[]
for r in [{"type":"turn.started"},
          {"type":"item.completed","item":{"id":"1","type":"agent_message","text":"hi"}},
          {"type":"turn.completed","usage":{"input_tokens":1}}]:
    evs+=c.feed(r)
assert any(e.kind==M.KIND_MESSAGE for e in evs) and evs[-1].kind in (M.KIND_DONE,M.KIND_USAGE)
cl=ClaudeNormalizer()
e2=cl.feed({"type":"assistant","session_id":"s","message":{"content":[{"type":"text","text":"x"}],"usage":{"output_tokens":1}}})
assert any(e.kind==M.KIND_MESSAGE for e in e2)
print("ok")
PY

echo "== 7. secret-leak audit (real turn end-to-end, then scan every persisted byte) =="
TMP7=$(mktemp -d); chown "$U" "$TMP7"
SECRET="ACCSECRET_$(date +%s)_a1b2c3d4e5f6"
run_u env XDG_STATE_HOME="$TMP7/s" XDG_CONFIG_HOME="$TMP7/c" SECRET="$SECRET" python3 - <<'PY' && ok "no plaintext secret in DB/events/raw logs" || no "SECRET LEAKED TO DISK"
import asyncio, os, sys, pathlib
from superterm.sessions.store import Store
from superterm.sessions import controller as C
from superterm.redaction import Redactor
from superterm import models as M
from superterm.drivers.base import RunResult
sec = os.environ["SECRET"]
store = Store()
store.set_redactor(Redactor([sec]))
ctrl = C.Controller(store, Redactor([sec]))

class Drv:
    provider = "codex"
    async def run(self, spec, emit, raw):
        await raw("stdout", '{"leak": "%s"}' % sec)          # raw stream carries it
        await emit(M.ev_message("assistant says " + sec, role="assistant"))
        await emit(M.ev_done("completed"))
        return RunResult(status="completed", exit_code=0)
    async def cancel(self): return True

orig = C.driver_for
C.driver_for = lambda p, redactor=None: Drv()
try:
    s = store.create_session("codex", "/tmp", title="leak-audit")
    async def go():
        await ctrl.start_turn(s.id, "prompt contains " + sec)
        for _ in range(300):
            if not ctrl.is_running(s.id): break
            await asyncio.sleep(0.01)
    asyncio.run(go())
finally:
    C.driver_for = orig

# scan EVERY file under the temp state dir, plus the sqlite raw bytes
leaks = []
root = pathlib.Path(os.environ["XDG_STATE_HOME"])
for p in root.rglob("*"):
    if p.is_file():
        try:
            if sec.encode() in p.read_bytes():
                leaks.append(str(p))
        except OSError:
            pass
# sqlite WAL may still hold the secret in the -wal file; force a checkpoint first
store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
for p in root.rglob("*"):
    if p.is_file():
        try:
            if sec.encode() in p.read_bytes():
                leaks.append(str(p))
        except OSError:
            pass
store.close()
if leaks:
    print("LEAKED IN:", leaks); sys.exit(1)
print("ok: scanned", len(list(root.rglob('*'))), "paths, no leak")
PY
rm -rf "$TMP7"

echo "== 8. invariants audit: exactly-one done + workspace escape + push version =="
run_u python3 - <<'PY' && ok "done-exactly-once / path-escape / push-version invariants" || no "invariant audit"
import asyncio, os, sys
from superterm.sessions.store import Store
from superterm.sessions.controller import Controller
from superterm.drivers.base import RunResult
from superterm import models as M
import tempfile, pathlib
tmp = tempfile.mkdtemp(); os.environ["XDG_STATE_HOME"] = tmp + "/s"
store = Store(); ctrl = Controller(store)
s = store.create_session("codex", "/tmp")

# (a) push failure on done must NOT duplicate done, must finalize turn
async def bad_push(msg): raise RuntimeError("down")
ctrl.set_push(bad_push)
import superterm.sessions.controller as C
class Drv:
    provider="codex"
    async def run(self, spec, emit, raw):
        await emit(M.ev_done("completed")); return RunResult(status="completed", exit_code=0)
    async def cancel(self): return True
orig=C.driver_for; C.driver_for=lambda p, redactor=None: Drv()
async def go():
    await ctrl.start_turn(s.id, "x")
    for _ in range(300):
        if not ctrl.is_running(s.id): break
        await asyncio.sleep(0.01)
asyncio.run(go()); C.driver_for=orig
dones=[e for e in store.all_events(s.id) if e.get("kind")==M.KIND_DONE]
assert len(dones)==1, f"expected 1 done, got {len(dones)}"
assert not ctrl.is_running(s.id), "active turn not cleaned"
row=store._conn.execute("SELECT status FROM turns WHERE session_id=?", (s.id,)).fetchone()
assert row and row[0]!="running", "turn not finalized"

# (b) push envelope carries protocol_version
got=[]
ctrl.set_push(lambda m: _ap(got, m))
async def _ap(lst, m): lst.append(m)
asyncio.run(ctrl._emit(M.ev_message("hi", role="assistant")))
assert got and got[0].get("protocol_version")==M.PROTOCOL_VERSION, got[:1]

# (c) workspace escapes rejected
from superterm.bridge import Bridge
from superterm.app import App
store.close()
app=App(); br=Bridge(app)
def call(m,p):
    import json; return json.loads(asyncio.run(br.handle(json.dumps({"protocol_version":1,"request_id":"1","method":m,"params":p}))))
for bad in ("/etc","/root","/"):
    r=call("workspace_list", {"root": bad}); assert not r["ok"] and r["error"]["code"]=="forbidden", (bad,r)
    r=call("session_create", {"provider":"codex","workspace":bad}); assert not r["ok"] and r["error"]["code"]=="forbidden", (bad,r)
app.close()
print("ok")
PY

echo "== 9. finalize durability audit: persistent DB write failure must never strand a turn =="
TMP9=$(mktemp -d); chown "$U" "$TMP9"
run_u env XDG_STATE_HOME="$TMP9/s" XDG_CONFIG_HOME="$TMP9/c" python3 - <<'PY' && ok "failed finalize journaled then healed at boot (no permanently-running turn)" || no "finalize durability"
import asyncio, os, sys
from superterm.sessions.store import Store
from superterm.sessions import controller as C
from superterm.drivers.base import RunResult
from superterm import models as M

store = Store()
sess = store.create_session("codex", "/tmp")

class Drv:
    provider = "codex"
    async def run(self, spec, emit, raw):
        await emit(M.ev_message("hi", role="assistant"))
        return RunResult(status="completed", exit_code=0)
    async def cancel(self): return True

def broken_finish(tid, status, exit_code=None):
    raise RuntimeError("disk read-only")
real_finish = store.finish_turn
store.finish_turn = broken_finish
ctrl = C.Controller(store)
orig = C.driver_for
C.driver_for = lambda p, redactor=None: Drv()
async def go():
    await ctrl.start_turn(sess.id, "hi")
    for _ in range(500):
        if not ctrl.is_running(sess.id): return
        await asyncio.sleep(0.01)
asyncio.run(go()); C.driver_for = orig

row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
assert row[0] == "running", f"expected stuck-running intermediate state, got {row[0]}"
jpath = store.recovery_journal_path()
assert jpath.exists(), "failed finalize must be journaled"
assert "completed" in jpath.read_text(encoding="utf-8"), "journal must carry intended status"

# simulate next boot
store.finish_turn = real_finish
store.recover_orphans()
row = store._conn.execute("SELECT status FROM turns WHERE session_id=?", (sess.id,)).fetchone()
assert row[0] == "completed", f"boot recovery must restore intended status, got {row[0]}"
assert store.get_session(sess.id).status == "active", "session must be released"
store.close()
print("ok")
PY
rm -rf "$TMP9"

echo "== SUMMARY: PASS=$PASS FAIL=$FAIL =="
[ "$FAIL" = 0 ] && echo "ACCEPTANCE_OK" || echo "ACCEPTANCE_HAS_FAILURES"
