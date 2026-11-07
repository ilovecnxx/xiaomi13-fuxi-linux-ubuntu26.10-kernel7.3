# SuperTerm 第六轮复审任务（只读）

第五轮结论为「**有条件通过**」，A（finalize 持久性）已判定修复成立。剩余两点：
1. 全仓库异步测试未统一：`tests/unit/test_store.py:101-105` 仍是「第一个 `asyncio.run()` 里 `start_turn`，
   另一 `asyncio.run()` 里 sleep 等待」的跨事件循环写法。
2. journal 写入未 `fsync`，底层存储整体不可写时无法保证 journal 落盘。

开发方已修复，请做**第六轮只读复审**，只核查这两点与回归。**只读，不要修改文件。**

## 本轮修复（请核实）

1. **`test_store.py` 改为同一事件循环**
   - `test_turn_persists_events` 现在把 `start_turn` 与「轮询到 turn 结束」放在**同一个 `asyncio.run(go())`** 内，
     并带 5s 上限断言。见 `tests/unit/test_store.py:98-112`。
   - 已全仓库扫描：不再存在 `asyncio.run(ctrl.start_turn(...))` 或「跨 run 等待」模式
     （剩余 `asyncio.run(...)` 均为单次调用：`sink(...)`、`drv.run(...)`、`bridge.handle(...)`、`ctrl._emit(...)`）。

2. **journal 增加 fsync**
   - `record_recovery()` 写入后 `flush()` + `os.fsync(fileno())`（`OSError` 吞掉，best-effort），
     确保崩溃/掉电后 journal 行已落盘。见 `src/superterm/sessions/store.py:243-270`。

## 当前证据
- 主机：`python3 tests/run_tests.py` → **Ran 56 tests, OK**（51 unit + 5 integration）。
- 真机：`tests/device/acceptance-evidence.txt` → **PASS=17 FAIL=0**，内嵌单测 51 项通过
  （含第 7 节落盘泄漏逐字节扫描、第 8 节不变量、第 9 节 finalize 持久性 journal→对账）。
- 真机 GUI：真实 claude 回合渲染进气泡（`E2E_POLL assistantText=ROUND5_GUI_OK`）。

## 输出
给出总体结论（通过/有条件通过/不通过）+ 逐条核查（附文件:行）+ 仍存在的问题。只读。
