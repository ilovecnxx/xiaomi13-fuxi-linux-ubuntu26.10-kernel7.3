/* 最小 uuid 头文件：仅为满足 EDK2 BaseTools 里未被使用的 #include。
 * BaseTools 不调用任何 uuid_* 函数，因此无需链接 libuuid。 */
#ifndef _STUB_UUID_UUID_H
#define _STUB_UUID_UUID_H
typedef unsigned char uuid_t[16];
#endif
