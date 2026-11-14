/* SPDX-License-Identifier: MIT */
/* Device-only EGL accounting and fault injection for one camera/render thread. */
#define _GNU_SOURCE
#define EGL_EGLEXT_PROTOTYPES
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned int attempts, created, destroyed, live, peak, injected, bad_destroy;
static unsigned int fbo_calls, draw_calls;
static int draw_error;
static PFNEGLCREATEIMAGEKHRPROC real_create;
static PFNEGLDESTROYIMAGEKHRPROC real_destroy;

static void *symbol(const char *name)
{
    void *result = dlsym(RTLD_NEXT, name);
    if (!result) {
        fprintf(stderr, "EGL_AUDIT missing_symbol=%s\n", name);
        abort();
    }
    return result;
}

static int fail_at(const char *name, unsigned int call)
{
    const char *value = getenv(name);
    if (!value)
        return 0;
    char *end;
    long target = strtol(value, &end, 10);
    if (!*value || *end || target == 0 || target < -1) {
        fprintf(stderr, "EGL_AUDIT invalid_setting=%s\n", name);
        abort();
    }
    if (target == -1 || (unsigned long)target == call) {
        injected++;
        return 1;
    }
    return 0;
}

static EGLImageKHR audit_create(EGLDisplay display, EGLContext context,
                                EGLenum target, EGLClientBuffer buffer,
                                const EGLint *attributes)
{
    attempts++;
    if (fail_at("EGL_AUDIT_FAIL_IMPORT", attempts))
        return EGL_NO_IMAGE_KHR;
    EGLImageKHR image = real_create(display, context, target, buffer, attributes);
    if (image != EGL_NO_IMAGE_KHR) {
        created++;
        live++;
        if (live > peak)
            peak = live;
    }
    return image;
}

static EGLBoolean audit_destroy(EGLDisplay display, EGLImageKHR image)
{
    EGLBoolean result = real_destroy(display, image);
    if (result && live) {
        destroyed++;
        live--;
    } else {
        bad_destroy++;
    }
    return result;
}

__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *name)
{
    PFNEGLGETPROCADDRESSPROC get_proc = (PFNEGLGETPROCADDRESSPROC)symbol("eglGetProcAddress");
    __eglMustCastToProperFunctionPointerType result = get_proc(name);
    if (!strcmp(name, "eglCreateImageKHR") && result) {
        real_create = (PFNEGLCREATEIMAGEKHRPROC)result;
        return (__eglMustCastToProperFunctionPointerType)audit_create;
    }
    if (!strcmp(name, "eglDestroyImageKHR") && result) {
        real_destroy = (PFNEGLDESTROYIMAGEKHRPROC)result;
        return (__eglMustCastToProperFunctionPointerType)audit_destroy;
    }
    return result;
}

GLenum glCheckFramebufferStatus(GLenum target)
{
    GLenum (*check)(GLenum) = symbol("glCheckFramebufferStatus");
    GLenum result = check(target);
    if (fail_at("EGL_AUDIT_FAIL_FBO", ++fbo_calls))
        return GL_FRAMEBUFFER_UNSUPPORTED;
    return result;
}

void glDrawArrays(GLenum mode, GLint first, GLsizei count)
{
    void (*draw)(GLenum, GLint, GLsizei) = symbol("glDrawArrays");
    draw(mode, first, count);
    draw_error = fail_at("EGL_AUDIT_FAIL_DRAW", ++draw_calls);
}

GLenum glGetError(void)
{
    GLenum (*get_error)(void) = symbol("glGetError");
    GLenum result = get_error();
    if (draw_error) {
        draw_error = 0;
        return GL_INVALID_OPERATION;
    }
    return result;
}

__attribute__((destructor)) static void report(void)
{
    fprintf(stderr, "EGL_AUDIT attempts=%u created=%u destroyed=%u live=%u peak=%u injected=%u bad_destroy=%u\n",
            attempts, created, destroyed, live, peak, injected, bad_destroy);
}
