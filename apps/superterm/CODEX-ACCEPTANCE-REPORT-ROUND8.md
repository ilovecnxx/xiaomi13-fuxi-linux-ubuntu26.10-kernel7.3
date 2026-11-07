我会按第八轮验收范围只读检查：先读取复审 brief，再对照超时校验实现和 `TestTimeoutPlumbing` 的调用链与断言，最后补看相关测试结果或代码中的回归风险。
初步实现显示 `Controller.start_turn` 已对 `None`、0 和负数回落到默认值，三个入口也都把超时送到 controller。我要进一步核对驱动实际使用的是哪一层实现，以及新增测试是否等到 fake driver 捕获到 spec 后才断言，避免“断言了空集合也通过”或只测到参数转发表象。
入口测试的非空约束是有效的：大多数用 `captured[0]`，若 fake driver 从未运行会直接失败；默认值测试还明确要求恰好捕获一次。发现一个边界值得在结论里限定：controller 调用链会把 0/负数规范化，但直接把 `AgentSpec(timeout_s=0)` 交给 driver 时，BaseDriver 和 DshDriver 仍会走无超时等待；我会用现有测试核实目标修复通过情况，再按应用入口与 driver 直调分别下结论。
**总体结论：有条件通过。** 经源码核对，controller 及 App、headless、bridge 的正常调用链会为 `timeout_s=0` 和负数采用默认超时；新增测试也会因 `captured[0]` 在未捕获 spec 时抛错，不会空集合通过。但直接调用 driver 时，0 仍可导致无限等待，因此“0/负数不能关闭超时”只在 controller 调用链成立。

1. **controller 的默认与非法值处理已生效。** [controller.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/controller.py:94) 在构造 `AgentSpec` 时，仅接受非空且大于 0 的显式值；否则采用 `DEFAULT_TURN_TIMEOUT_S`。[base.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/drivers/base.py:133) 对正值启用 `wait_for`。

2. **driver 直调仍有边界问题。** `BaseDriver.run` 对 `timeout_s=0` 或负数会进入无超时的 `proc.wait()` 分支；[DshDriver.run](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/drivers/dsh.py:76) 对 0 也会无限等待。负值在 DshDriver 中会传给 `wait_for`，可能立即超时。经 controller 的产品入口会先规范化这些值，但直接使用 driver 和 `AgentSpec` 的调用方不受保护。

3. **入口传参与非空断言符合 brief。** [TestTimeoutPlumbing](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:421) 覆盖未指定默认值以及 0、-1；App、bridge、headless 分别验证 7、9、11.0 的传参（[447 行](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:447)、[464 行](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:464)、[489 行](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:489)）。这些测试访问 `captured[0]`，没有捕获时会失败；默认值测试还要求捕获数恰为 1。实际转发分别位于 [app.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/app.py:81)、[bridge.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/bridge.py:134) 和 [headless.py](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/headless.py:94)。

4. **回归验证受只读环境限制。** 我尝试运行 `PYTHONDONTWRITEBYTECODE=1 python3 tests/run_tests.py`，62 项中 41 项因环境没有可写临时目录而报错，不能据此判定测试失败或通过。brief 记录的主机及真机通过结果是现有证据，但本轮无法独立复现。

仍需处理的是 driver 直调时 0 关闭超时的边界；否则，controller 入口的实现和新增测试结构符合本轮验收要求。
[TURN COMPLETE]