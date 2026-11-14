/* SPDX-License-Identifier: MIT */
/* Exercise the real DebayerEGL start/stop without opening a camera device. */
#include <libcamera/formats.h>
#include <libcamera/stream.h>

#include "libcamera/internal/global_configuration.h"
#include "libcamera/internal/software_isp/swstats_cpu.h"
#include "../src/libcamera/software_isp/debayer_egl.h"

#include <cstdlib>
#include <dlfcn.h>
#include <iostream>
#include <memory>
#include <string>

using namespace libcamera;

int main(int argc, char **argv)
{
    if (argc != 2 || (std::string(argv[1]) != "normal" && std::string(argv[1]) != "failure"))
        return 2;
    const unsigned int expectedFailures = std::string(argv[1]) == "failure" ? 1 : 0;
    auto checkpoint = reinterpret_cast<int (*)()>(dlsym(RTLD_DEFAULT, "fuxi_egl_checkpoint"));
    if (!checkpoint)
        return 2;

    // Bypass audit counters for another owner of the same EGL display.
    void *eglLibrary = dlopen("libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!eglLibrary)
        return 2;
    auto create = reinterpret_cast<PFNEGLCREATECONTEXTPROC>(dlsym(eglLibrary, "eglCreateContext"));
    auto choose = reinterpret_cast<PFNEGLCHOOSECONFIGPROC>(dlsym(eglLibrary, "eglChooseConfig"));
    auto current = reinterpret_cast<PFNEGLMAKECURRENTPROC>(dlsym(eglLibrary, "eglMakeCurrent"));
    auto destroy = reinterpret_cast<PFNEGLDESTROYCONTEXTPROC>(dlsym(eglLibrary, "eglDestroyContext"));
    if (!create || !choose || !current || !destroy)
        return 2;
    EGLDisplay sharedDisplay = EGL_NO_DISPLAY;
    EGLContext otherContext = EGL_NO_CONTEXT;

    GlobalConfiguration configuration;
    auto stats = std::make_unique<SwStatsCpu>(configuration);
    if (!stats->isValid())
        return 2;
    DebayerEGL debayer(std::move(stats), configuration);
    StreamConfiguration input, output;
    input.pixelFormat = formats::SBGGR10_CSI2P;
    input.size = Size(640, 480);
    input.stride = 800;
    output.pixelFormat = formats::ABGR8888;
    output.size = debayer.sizes(input.pixelFormat, input.size).max;
    std::tie(output.stride, output.frameSize) =
        debayer.strideAndFrameSize(output.pixelFormat, output.size);
    if (debayer.configure(input, {output}, true))
        return 2;

    bool valid = true;
    unsigned int failures = 0;
    for (unsigned int round = 0; round < 100; ++round) {
        int ret = debayer.start();
        if (ret) {
            failures++;
            // Failed starts must release resources before the caller stops.
            valid &= checkpoint() == 0;
        } else if (otherContext == EGL_NO_CONTEXT) {
            std::cout << "EGL_RENDERER " << glGetString(GL_RENDERER) << std::endl;
            sharedDisplay = eglGetCurrentDisplay();
            const EGLint attributes[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                                         EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_NONE};
            const EGLint contextAttributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
            EGLConfig config;
            EGLint count = 0;
            if (choose(sharedDisplay, attributes, &config, 1, &count) && count)
                otherContext = create(sharedDisplay, config, EGL_NO_CONTEXT, contextAttributes);
            valid &= otherContext != EGL_NO_CONTEXT;
        }
        debayer.stop();
        valid &= checkpoint() == 0;
        valid &= eglGetCurrentContext() == EGL_NO_CONTEXT;
        // A second stop must not delete stale shader/program handles.
        debayer.stop();
        valid &= checkpoint() == 0;
        if (otherContext != EGL_NO_CONTEXT) {
            bool usable = current(sharedDisplay, EGL_NO_SURFACE, EGL_NO_SURFACE, otherContext);
            valid &= usable;
            if (usable)
                valid &= glGetString(GL_VERSION) != nullptr;
            valid &= current(sharedDisplay, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        }
    }
    valid &= otherContext != EGL_NO_CONTEXT;
    if (otherContext != EGL_NO_CONTEXT)
        valid &= destroy(sharedDisplay, otherContext);
    valid &= failures == expectedFailures;
    std::cout << "EGL_RESTART rounds=100 failures=" << failures
              << " result=" << (valid ? "PASS" : "FAIL") << std::endl;
    return valid ? 0 : 1;
}
