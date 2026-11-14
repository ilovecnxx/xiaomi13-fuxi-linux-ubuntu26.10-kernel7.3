# libcamera 软件 ISP 首帧初始化候选

本候选固定于 libcamera v0.7.0，未加入标准支持包、APT 或发布配方。
源码与补丁哈希见 `manifest.json`。设备原有 libcamera 库不替换。

## 问题与范围

`SoftwareIsp::process()` 异步请求 IPA 参数后，立即把缓存按值送往
Debayer 工作线程。缓存首次使用前没有初始化；参数回调到达时，已经排队的
首帧参数副本不会随缓存更新。实机第 0 帧已观察到无效 gamma；使用 glibc
`MALLOC_PERTURB_=191` 后，黑电平为 3.00392151，gamma 表索引为 3076，
在 `DebayerCpu::updateGammaTable()` 触发 1024 项数组断言。

补丁在同步 `ipa_->init()` 成功后复制共享参数到缓存；同时将 IPA 初始
颜色矩阵设为单位阵，保证初始参数每个字段都有定义。
不改 gamma 索引规则、曝光、带宽、内核或后续帧的异步调度。
后续帧参数对应关系、GPU 停帧及 MM0 阻塞仍是独立待验收项。

## 隔离构建与对照

在原生 arm64 Ubuntu 上准备编译器、Meson、Ninja、pkg-config、
Python YAML/Jinja2/PLY、libyaml/libevent/libssl/libudev/libyuv/libjpeg
开发包。实验开启断言，使用 CPU 软件 ISP；不执行 `ninja install`。
`-Dtest=true` 会额外启用上游测试所需的 vimc/virtual 管线。

先核对固定源码归档及补丁。`verify.py` 需要 Python 3.11+ 和 Git，只在临时目录
重放涉及的两个源码文件，不改变现有检出或安装系统库：

```sh
# 在 libcamera 上游仓库中生成无前缀归档（不要用压缩后的下载包替代）
git archive --format=tar -o /tmp/libcamera-0.7.0-source.tar b7854fd07d42168f099b5ce30d1702e0e0875bf5
# 回到本项目根目录
python3 packaging/experiments/libcamera-first-frame/verify.py /tmp/libcamera-0.7.0-source.tar
```

本地恢复归档为 `work/libcamera-0.7.0-source.tar`。核验包括归档 SHA256、归档内
commit、补丁 SHA256、正向应用与反向检查；不代替编译或采集验收。
在该固定提交的独立干净检出中先构建未修改的基线。对照实验使用两个分别
从归档解出的目录 `baseline/` 和 `patched/`；只在后者应用补丁，各自执行：

```sh
meson setup build --wrap-mode=nodownload --buildtype=debugoptimized \
    -Dpipelines=simple -Dipas=simple -Dcam=enabled -Dqcam=disabled \
    -Dgstreamer=disabled -Dpycamera=disabled -Ddocumentation=disabled \
    -Dlc-compliance=disabled -Dv4l2=disabled -Dtracing=disabled \
    -Dtest=true -Dcpp_args=-D_GLIBCXX_ASSERTIONS
ninja -C build -j3
meson test -C build --no-rebuild --print-errorlogs --num-processes 2
```

在已完成内核健康预检、保留默认/回退启动条目的设备上，以普通用户运行：

```sh
LIBCAMERA_PIPELINES_MATCH_LIST=simple \
timeout -k 3s 35s gdb -q -batch -x /path/to/verify-first-frame.gdb \
    --args build/src/apps/cam/cam -c 1 \
    -s role=viewfinder,width=4208,height=3120 -C5
```

检查脚本在第 0 帧检验黑电平、gamma、对比度、增益和完整单位阵；还要求
正常完成采集。原版预期失败，应用补丁并重新构建后预期通过。GDB 改变线程时序，
故仍需脱离调试器、保持内存扰动的连续启停复测。设备卡在内核 D 状态时，
用户态 timeout 不能保证终止，不能把超时当作完整的故障恢复。

补丁版在配置和构建前执行
`git apply /path/to/0001-seed-software-isp-parameters.patch`，其余构建参数一致。

构建目录中的 cam 通过构建时 RPATH 与 libcamera 的源码目录检测使用本地库、
IPA 和配置。运行前应核对 `ldd` 与 IPA 日志，避免混用系统库。
历史原版编译/GDB 日志保存在忽略的 `work/camera-hfclk-20261112/`。

## 普通进程重复采集

`capture-matrix.sh` 是本机型的设备端实验工具，以 root 做只读内核检查，
每次 `cam` 通过 `runuser` 以 `fuxi` 用户运行。要求运行 cam6hfclk、下次默认
预设 deb9、回退 deb8；不会自行切换内核或安装库。启动前仍须按项目维护说明
核对启动文件、自动确认及恢复条件，不能在 deb9 上直接运行本实验。

```sh
# 在手机上，传入补丁版 build 的绝对路径及尚不存在的日志目录
bash /path/to/capture-matrix.sh /path/to/patched/build /var/tmp/camera-matrix 6
```

每轮依次采集半尺寸 300 帧、全尺寸 120 帧，轮间空闲 10 秒，轮换
`MALLOC_PERTURB_=191/85/255`。脚本核对构建目录的动态库、OV13B10 枚举、
实际返回帧数、退出码、CAMSS runtime suspend、HF 门控引用及内核故障标记。
失败立即停止后续轮次；日志目录必须新建，权限 0700，不保存图像。
单次用户态采集使用 35 秒 timeout，但 D 状态仍可能需要现场恢复；外层 SSH
超时也不等于设备进程已被终止。脚本不自动重启、不清 BCB。

## 验证记录

2026-11-14 恢复 USB SSH 后已完成手机原生构建和 GDB A/B：原版首帧参数
检查失败，补丁版全部参数符合初值且完成 5 帧后正常退出。两版上游测试均为
45 通过、1 项预期失败、29 项跳过、0 项失败；不能把缺少测试设备的跳过项
计为通过。本轮完整采集和恢复结果见
[首帧修复验收](../../../docs/相机首帧修复验收-20261114.md)。
本目录仍为隔离实验，不是系统安装入口，也未加入标准 APT 配方。
