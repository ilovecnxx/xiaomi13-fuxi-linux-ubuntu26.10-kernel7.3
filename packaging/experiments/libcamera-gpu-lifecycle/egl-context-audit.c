/* SPDX-License-Identifier: MIT */
/* One software ISP worker; inspect counters only after Camera::stop(). */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static unsigned int contexts, context_creates, context_destroys;
static unsigned int shaders, programs, invalid_deletes, injected;
static unsigned int create_calls, current_calls, config_calls, shader_calls, link_calls;

static void *symbol(const char *name)
{
    void *result = dlsym(RTLD_NEXT, name);
    if (!result) {
        fprintf(stderr, "EGL_CONTEXT missing_symbol=%s\n", name);
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
    unsigned long target = strtoul(value, &end, 10);
    if (!*value || *end || !target) {
        fprintf(stderr, "EGL_CONTEXT invalid_setting=%s\n", name);
        abort();
    }
    if (target == call) {
        injected++;
        return 1;
    }
    return 0;
}

EGLContext eglCreateContext(EGLDisplay display, EGLConfig config, EGLContext share,
                            const EGLint *attributes)
{
    PFNEGLCREATECONTEXTPROC create = symbol("eglCreateContext");
    if (fail_at("EGL_CONTEXT_FAIL_CREATE", ++create_calls))
        return EGL_NO_CONTEXT;
    EGLContext context = create(display, config, share, attributes);
    if (context != EGL_NO_CONTEXT) {
        contexts++;
        context_creates++;
    }
    return context;
}

EGLBoolean eglDestroyContext(EGLDisplay display, EGLContext context)
{
    PFNEGLDESTROYCONTEXTPROC destroy = symbol("eglDestroyContext");
    PFNEGLGETCURRENTCONTEXTPROC current = symbol("eglGetCurrentContext");
    if (current() == context)
        invalid_deletes++;
    EGLBoolean result = destroy(display, context);
    if (result && contexts) {
        contexts--;
        context_destroys++;
    } else {
        invalid_deletes++;
    }
    return result;
}

EGLBoolean eglMakeCurrent(EGLDisplay display, EGLSurface draw, EGLSurface read, EGLContext context)
{
    PFNEGLMAKECURRENTPROC make_current = symbol("eglMakeCurrent");
    if (context != EGL_NO_CONTEXT && fail_at("EGL_CONTEXT_FAIL_CURRENT", ++current_calls))
        return EGL_FALSE;
    return make_current(display, draw, read, context);
}

EGLBoolean eglChooseConfig(EGLDisplay display, const EGLint *attributes, EGLConfig *configs,
                           EGLint size, EGLint *count)
{
    PFNEGLCHOOSECONFIGPROC choose = symbol("eglChooseConfig");
    EGLBoolean result = choose(display, attributes, configs, size, count);
    if (fail_at("EGL_CONTEXT_FAIL_CONFIG", ++config_calls)) {
        *count = 0;
        return EGL_TRUE;
    }
    return result;
}

GLuint glCreateShader(GLenum type)
{
    GLuint (*create)(GLenum) = symbol("glCreateShader");
    GLuint id = create(type);
    shaders += !!id;
    return id;
}

void glDeleteShader(GLuint id)
{
    GLboolean (*is_shader)(GLuint) = symbol("glIsShader");
    void (*destroy)(GLuint) = symbol("glDeleteShader");
    if (id && is_shader(id) && shaders)
        shaders--;
    else
        invalid_deletes++;
    destroy(id);
}

void glGetShaderiv(GLuint id, GLenum pname, GLint *value)
{
    void (*query)(GLuint, GLenum, GLint *) = symbol("glGetShaderiv");
    query(id, pname, value);
    if (pname == GL_COMPILE_STATUS && fail_at("EGL_CONTEXT_FAIL_SHADER", ++shader_calls))
        *value = GL_FALSE;
}

GLuint glCreateProgram(void)
{
    GLuint (*create)(void) = symbol("glCreateProgram");
    GLuint id = create();
    programs += !!id;
    return id;
}

void glDeleteProgram(GLuint id)
{
    GLboolean (*is_program)(GLuint) = symbol("glIsProgram");
    void (*destroy)(GLuint) = symbol("glDeleteProgram");
    if (id && is_program(id) && programs)
        programs--;
    else
        invalid_deletes++;
    destroy(id);
}

void glGetProgramiv(GLuint id, GLenum pname, GLint *value)
{
    void (*query)(GLuint, GLenum, GLint *) = symbol("glGetProgramiv");
    query(id, pname, value);
    if (pname == GL_LINK_STATUS && fail_at("EGL_CONTEXT_FAIL_LINK", ++link_calls))
        *value = GL_FALSE;
}

int fuxi_egl_checkpoint(void)
{
    fprintf(stderr, "EGL_CONTEXT created=%u destroyed=%u live=%u shaders=%u programs=%u invalid_deletes=%u injected=%u\n",
            context_creates, context_destroys, contexts, shaders, programs, invalid_deletes, injected);
    return contexts || shaders || programs || invalid_deletes;
}
