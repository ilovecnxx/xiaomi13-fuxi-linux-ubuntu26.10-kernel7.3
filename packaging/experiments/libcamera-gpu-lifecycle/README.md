# GPU 软件 ISP 图像生命周期候选

在 libcamera v0.7.0 首帧初始化补丁之上修复 EGL image 泄漏及转换错误帧的
缓冲/请求回收。保持隔离构建，不安装系统库，不加入标准内核或 APT 配方。
源码和有序补丁 SHA256 固定在 `manifest.json`。

## 修复范围

- `DebayerEGL::debayerGPU()` 检查 DMA-BUF 导入结果，在绘制成功和失败路径
  都先等待 GPU 完成，再释放本帧的 EGL image。
- EGL image 句柄初始化为空；FBO 失败后释放并清空句柄，避免重复销毁。
- GPU 转换失败时设置 `FrameError`、清零 `bytesused`、结束 CPU DMA 同步并归还
  输入/输出缓冲。
- simple 管线对软件转换错误帧取消元数据等待，否则没有统计回调时请求仍会卡住。

本补丁不解决 EGL context 反复 start/stop 的生命周期、CPU mmap 失败回收、
GPU 裁切/颜色、MM0/RPMh、系统集成或其他传感器驱动。独立进程反复启停不能
替代同进程重启和系统挂起验收。

## 构建

```sh
python3 packaging/experiments/libcamera-gpu-lifecycle/verify.py work/libcamera-0.7.0-source.tar
# 在固定源码的全新解包中，依次应用 manifest.json 中两个补丁。
git apply /absolute/path/libcamera-first-frame/0001-seed-software-isp-parameters.patch
git apply /absolute/path/libcamera-gpu-lifecycle/0002-release-egl-images-and-return-failed-buffers.patch
```

原生 ARM64 构建参数、依赖与上游测试命令沿用
[首帧候选](../libcamera-first-frame/README.md)。不执行 `ninja install`。
`verify.py` 只校验归档并重放补丁，不声称编译或实机通过。

## 实机验收工具

`egl-audit.c` 是专用于单相机、单渲染线程的 LD_PRELOAD 测试库，不是部署组件。
它计数 EGL image 创建/销毁，注入导入、FBO 和绘制错误。只用于本目录的
`cam` 实验，不注入桌面或其他多线程 EGL 应用。它不统计 Mesa 内部分配、GL texture、
context 或整个进程的资源。

```sh
cc -shared -fPIC -Wall -Wextra -Werror -O2 egl-audit.c -o /var/tmp/fuxi-egl-audit.so -ldl
bash capture-matrix.sh /absolute/path/build /var/tmp/fuxi-egl-audit.so /var/tmp/new-gpu-logs
```

脚本要求 cam6hfclk，默认预设 deb9，保留 deb8 回退；root 执行预检，普通
`fuxi` 用户采集。先做三类错误各一次/连续 60 帧，再做三轮半尺寸/全尺寸各
900 帧及全尺寸 3,600 帧。每次检查退出码、实际完成帧、错误空缓冲数量、
创建/销毁配对、存活峰值和 CAMSS/HF 释放，失败停止。
采集日志用新建 0700 目录，未保存图像。timeout 不能保证结束内核 D 状态任务。

`check-capture.py` 可单独检查日志，但进程退出码与内核状态必须由调用者验证。
错误帧根据空缓冲计数；`cam` 日志不包含 `FrameMetadata::status`，因此不能
单凭日志独立验证 FrameError 枚举值。正常矩阵在脚本中要求退出码为 0。

原版对照使用已经带首帧补丁、尚无 GPU 补丁的构建；例如：

```sh
LIBCAMERA_PIPELINES_MATCH_LIST=simple LIBCAMERA_SOFTISP_MODE=gpu \
timeout -s INT -k 3s 25s env LD_PRELOAD=/var/tmp/fuxi-egl-audit.so \
    /absolute/path/baseline/build/src/apps/cam/cam -c 1 \
    -s role=viewfinder,width=2104,height=1560 -C30
```

2026-11-14 原版 30 帧 `created=30 destroyed=0 live=30`，补丁版首轮 120 帧
`created=120 destroyed=120 live=0 peak=1`。完整结果见
[GPU 验收](../../../docs/相机GPU回收验收-20261114.md)。泄漏与错误路径修复有
独立证据，不等于历史自然停帧或互连阻塞的根因已全部解决。
