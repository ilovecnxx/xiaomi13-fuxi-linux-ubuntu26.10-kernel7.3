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
在该固定提交的独立干净检出中先构建未修改的基线：

```sh
meson setup build --wrap-mode=nodownload --buildtype=debugoptimized \
    -Dpipelines=simple -Dipas=simple -Dcam=enabled -Dqcam=disabled \
    -Dgstreamer=disabled -Dpycamera=disabled -Ddocumentation=disabled \
    -Dlc-compliance=disabled -Dv4l2=disabled -Dtracing=disabled \
    -Dtest=true -Dcpp_args=-D_GLIBCXX_ASSERTIONS
ninja -C build -j3
```

在已完成内核健康预检、保留默认/回退启动条目的设备上，以普通用户运行：

```sh
timeout -k 3s 35s gdb -q -batch -x /path/to/verify-first-frame.gdb \
    --args build/src/apps/cam/cam -c 1 \
    -s role=viewfinder,width=4208,height=3120 -C5
```

检查脚本在第 0 帧检验黑电平、gamma、对比度、增益和完整单位阵；还要求
正常完成采集。原版预期失败，应用补丁并重新构建后预期通过。GDB 改变线程时序，
故仍需脱离调试器、保持内存扰动的连续启停复测。设备卡在内核 D 状态时，
用户态 timeout 不能保证终止，不能把超时当作完整的故障恢复。

```sh
git apply /path/to/0001-seed-software-isp-parameters.patch
ninja -C build -j3
```

构建目录中的 cam 通过构建时 RPATH 与 libcamera 的源码目录检测使用本地库、
IPA 和配置。运行前应核对 `ldd` 与 IPA 日志，避免混用系统库。
历史原版编译/GDB 日志保存在忽略的 `work/camera-hfclk-20261112/`。
2026-11-14 只做离线哈希与补丁重放核验；宿主机缺少 libevent/yaml/libyuv/libjpeg
开发依赖，且无法连机，未完成补丁后编译和设备 A/B 验收。
实际结果见 [工程收尾记录](../../../docs/工程收尾-20261114.md)；本目录不是系统安装入口。
