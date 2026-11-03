我会先读第九轮复审说明和相关实现/测试，再沿调用路径核对 `BaseDriver.run`、`DshDriver.run` 的超时处理，并判断新增测试是否能捕获未 clamp 的回归。全程只读。
说明里的核心修复在工作树中可见。我现在检查函数实际控制流、子进程清理与 done 事件的产生方式，以及测试的隔离/超时逻辑；同时确认无参数 `None`、零和负数是否都覆盖到直接驱动调用。
**总体结论：有条件通过。** 对 `timeout_s=0`、`None` 和负数，两个直接调用路径的实现都使用 `models.effective_timeout()`，会回退到正的默认超时；但新增测试没有覆盖 `DshDriver` 的 `None` 和负数，且有效超时函数仍会接受正无穷，无法保证所有输入都得到有限超时。

逐条核查：

- [`models.py`](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/models.py:166) 将默认值设为 `900.0`，并在调用时读取环境变量。`effective_timeout()` 会将 `None`、零、负数和无法转换的值回退到默认值；正数原样返回。
- [`base.py`](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/drivers/base.py:132) 在启动子进程后对 `spec.timeout_s` 调用 `M.effective_timeout()`，并无条件用 `asyncio.wait_for()` 等待进程。超时后会终止进程并发出一个 done 事件。
- [`dsh.py`](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/drivers/dsh.py:75) 采用同样的 clamp 和 `wait_for` 逻辑。实现上 `None` 和负数也会经过同一 helper 并有界等待。
- [`test_acceptance_fixes.py`](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/tests/unit/test_acceptance_fixes.py:523) 验证 helper 对 `None`、零、负数和正数的归一化；BaseDriver 测试分别直接传入 `0`、`None`、`-1`，运行 `sleep 60` 并核对耗时和恰好一个 done。该测试能区分旧逻辑，但如果超时处理退化，需等子进程满 60 秒才会触发耗时断言。DshDriver 测试只直接覆盖了 `0`（第 553 行起），未覆盖 `None` 和负数。
- [`controller.py`](/home/user/文档/deepseek-harness/default-workspace/linux-android/work/superterm/src/superterm/sessions/controller.py:102) 也在入口处调用同一 helper，与直接调用 driver 的防御性处理一致。

仍存在的问题：

- `effective_timeout()` 接受 `float("inf")`，因为它满足 `v > 0`；若直接调用传入正无穷，或环境变量配置为 `inf`，`wait_for` 将没有实际时间上限。这不影响本轮指定的零、`None`、负数结论，但与“始终有界”的目标不符。
- DshDriver 的新增回归测试没有覆盖 `None` 和负数；当前实现对它们的处理正确，但测试无法独立防止这两种输入今后回归。

本次按只读要求检查代码与现有验收证据，未运行测试；简报所列的测试通过结果未在本轮复跑。
[TURN COMPLETE]