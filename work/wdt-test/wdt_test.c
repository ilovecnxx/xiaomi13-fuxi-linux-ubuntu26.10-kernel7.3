#include <stdio.h>
#include <stdint.h>

static void do_smc(uint64_t id, uint64_t a1, uint64_t a2,
                   uint64_t *r0, uint64_t *r1, uint64_t *r2)
{
    register uint64_t x0 asm("x0") = id;
    register uint64_t x1 asm("x1") = a1;
    register uint64_t x2 asm("x2") = a2;
    asm volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2) : : "x3","x4","x5","x6","x7","memory");
    *r0 = x0; *r1 = x1; *r2 = x2;
}

int main(void)
{
    uint64_t r0, r1, r2;
    /* 与 drivers/watchdog/gunyah_wdt.c 完全一致的函数 ID */
    struct { const char *name; uint64_t id; } calls[] = {
        { "GUNYAH_WDT_STATUS 0x86000006", 0x86000006UL },
        { "GUNYAH_WDT_CONTROL 0x86000005 (disable)", 0x86000005UL },
    };
    for (unsigned i = 0; i < sizeof(calls)/sizeof(calls[0]); i++) {
        do_smc(calls[i].id, i == 1 ? 2UL : 0UL, 0, &r0, &r1, &r2);
        printf("%s -> a0=%lld a1=%lld a2=%lld\n", calls[i].name,
               (long long)r0, (long long)r1, (long long)r2);
    }
    return 0;
}
