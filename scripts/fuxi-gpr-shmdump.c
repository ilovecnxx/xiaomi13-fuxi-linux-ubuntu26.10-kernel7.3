// 在 Android 里附着音频 HAL 进程，拦截它向 GPR 通道（默认 fd 8）的 write()，
// 记录每个 GPR 包；遇到 0x01001008 / 0x0100100c / 带外 SET_CFG（负载 16 B）时，
// 在 write 进入内核之前把进程里所有 "/dmabuf:" 读写映射整块转存到 outdir。
// 用法: fuxi-gpr-shmdump <pid> <outdir> [fd]   （root；Ctrl-C 或目标退出时结束并脱离）
// 编译: aarch64-linux-gnu-gcc -O2 -static -o fuxi-gpr-shmdump fuxi-gpr-shmdump.c
// 只读目标进程内存，不改寄存器、不改内存；每次 write 约增加几十微秒。
#define _GNU_SOURCE
#include <asm/ptrace.h>
#include <dirent.h>
#include <elf.h>
#include <sys/ioctl.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define MAXT 256
static pid_t tids[MAXT];
static int in_sys[MAXT];
static int ntids;
static volatile sig_atomic_t stop_flag;
static int gpr_fd = 8;
static pid_t target;
static const char *outdir;
static FILE *logf;
static int seq;

static int slot(pid_t t, int add)
{
	for (int i = 0; i < ntids; i++)
		if (tids[i] == t)
			return i;
	if (!add || ntids >= MAXT)
		return -1;
	tids[ntids] = t;
	in_sys[ntids] = 0;
	return ntids++;
}

static int memfd = -1;

/* process_vm_readv 读不了 dmabuf 映射（实测 -1），回退到 /proc/<pid>/mem */
static ssize_t rd(pid_t t, uint64_t addr, void *buf, size_t len)
{
	struct iovec l = { buf, len }, r = { (void *)addr, len };
	ssize_t n = process_vm_readv(t, &l, 1, &r, 1, 0);

	if (n == (ssize_t)len)
		return n;
	if (memfd >= 0) {
		ssize_t m = pread(memfd, buf, len, (off_t)addr);

		if (m > 0)
			return m;
		return -errno;
	}
	return n < 0 ? -errno : n;
}

/* dmabuf 映射不能从外部按地址读（process_vm_readv=-1，/proc/pid/mem=EIO）。
 * 办法：按映射的 inode 在目标进程的 fd 表里找到同一个 dmabuf，用 pidfd_getfd 复制到本进程，自己 mmap 读。 */
#include <sys/mman.h>
#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif
#ifndef SYS_pidfd_getfd
#define SYS_pidfd_getfd 438
#endif
struct dmabuf_sync { uint64_t flags; };
#define DMABUF_IOCTL_SYNC _IOW('b', 0, struct dmabuf_sync)
#define DMABUF_SYNC_READ (1 << 0)
#define DMABUF_SYNC_START (0 << 2)
#define DMABUF_SYNC_END (1 << 2)

static int find_target_fd(unsigned long long ino)
{
	char d[64], p[384];
	struct dirent *e;
	struct stat st;
	DIR *dir;
	int found = -1;

	snprintf(d, sizeof d, "/proc/%d/fd", target);
	dir = opendir(d);
	if (!dir)
		return -1;
	while ((e = readdir(dir))) {
		snprintf(p, sizeof p, "%s/%s", d, e->d_name);
		if (e->d_name[0] != '.' && stat(p, &st) == 0 && (unsigned long long)st.st_ino == ino) {
			found = atoi(e->d_name);
			break;
		}
	}
	closedir(dir);
	return found;
}

static ssize_t read_dmabuf(unsigned long long ino, unsigned long long off, size_t len, void **out)
{
	int pfd = syscall(SYS_pidfd_open, target, 0);
	int tfd = find_target_fd(ino);
	int mine;
	void *m;
	struct dmabuf_sync sy;
	ssize_t r = -ENOENT;

	if (pfd < 0)
		return -errno;
	if (tfd < 0) {
		close(pfd);
		return -ENOENT;
	}
	mine = syscall(SYS_pidfd_getfd, pfd, tfd, 0);
	close(pfd);
	if (mine < 0)
		return -errno;
	m = mmap(NULL, len, PROT_READ, MAP_SHARED, mine, off);
	if (m == MAP_FAILED) {
		r = -errno;
		close(mine);
		return r;
	}
	sy.flags = DMABUF_SYNC_START | DMABUF_SYNC_READ;
	ioctl(mine, DMABUF_IOCTL_SYNC, &sy);
	*out = malloc(len);
	if (*out) {
		memcpy(*out, m, len);
		r = len;
	} else {
		r = -ENOMEM;
	}
	sy.flags = DMABUF_SYNC_END | DMABUF_SYNC_READ;
	ioctl(mine, DMABUF_IOCTL_SYNC, &sy);
	munmap(m, len);
	close(mine);
	return r;
}

static void dump_maps(int n, uint32_t op)
{
	char path[64], line[512];
	snprintf(path, sizeof path, "/proc/%d/maps", target);
	FILE *m = fopen(path, "r");
	if (!m)
		return;
	int k = 0;
	while (fgets(line, sizeof line, m)) {
		unsigned long long a, b, off, ino;
		char perm[8], dev[16];
		if (!strstr(line, "/dmabuf:") ||
		    sscanf(line, "%llx-%llx %7s %llx %15s %llu", &a, &b, perm, &off, dev, &ino) != 6 || perm[0] != 'r')
			continue;
		size_t len = b - a;
		void *buf = NULL;
		if (len > (8u << 20))
			continue;
		ssize_t got = read_dmabuf(ino, off, len, &buf);
		char fn[256];
		snprintf(fn, sizeof fn, "%s/pkt%04d-op%08x-map%d-%llx.bin", outdir, n, op, k, a);
		if (got > 0) {
			FILE *o = fopen(fn, "wb");
			if (o) {
				fwrite(buf, 1, got, o);
				fclose(o);
			}
		}
		fprintf(logf, "   map%d va=%llx-%llx off=%llx ino=%llu perm=%s read=%zd\n", k, a, b, off, ino, perm, got);
		free(buf);
		k++;
	}
	fclose(m);
}

static void on_write(pid_t t, uint64_t buf, uint64_t count)
{
	uint8_t h[64];
	if (count < 24 || count > (1u << 20))
		return;
	size_t want = count < sizeof h ? count : sizeof h;
	if (rd(t, buf, h, want) < 24)
		return;
	uint32_t src, dst, tok, op, w0;
	memcpy(&w0, h, 4);
	memcpy(&src, h + 8, 4);
	memcpy(&dst, h + 12, 4);
	memcpy(&tok, h + 16, 4);
	memcpy(&op, h + 20, 4);
	size_t plen = count - 24;
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	int n = seq++;
	fprintf(logf, "#%d %ld.%06ld tid=%d op=%08x src=%x dst=%x tok=%x len=%zu payload:", n, (long)ts.tv_sec,
		ts.tv_nsec / 1000, t, op, src, dst, tok, plen);
	for (size_t i = 24; i < want; i++)
		fprintf(logf, " %02x", h[i]);
	fputc('\n', logf);
	int big = op == 0x01001008 || op == 0x0100100c || (op == 0x01001006 && plen == 16);
	if (big) {
		char fn[256];
		snprintf(fn, sizeof fn, "%s/pkt%04d-op%08x.bin", outdir, n, op);
		FILE *o = fopen(fn, "wb");
		if (o) {
			fwrite(h, 1, want, o);
			fclose(o);
		}
		dump_maps(n, op);
	}
	fflush(logf);
}

static void sig(int s)
{
	(void)s;
	stop_flag = 1;
}

static void seize_threads(void)
{
	char d[64];
	snprintf(d, sizeof d, "/proc/%d/task", target);
	DIR *dir = opendir(d);
	struct dirent *e;
	if (!dir)
		return;
	while ((e = readdir(dir))) {
		pid_t t = atoi(e->d_name);
		if (t <= 0 || slot(t, 0) >= 0)
			continue;
		if (ptrace(PTRACE_SEIZE, t, 0, PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACECLONE) == 0) {
			slot(t, 1);
			ptrace(PTRACE_INTERRUPT, t, 0, 0);
		}
	}
	closedir(dir);
}

int main(int argc, char **argv)
{
	if (argc < 3) {
		fprintf(stderr, "用法: %s <pid> <outdir> [fd]\n", argv[0]);
		return 2;
	}
	target = atoi(argv[1]);
	outdir = argv[2];
	if (argc > 3)
		gpr_fd = atoi(argv[3]);
	mkdir(outdir, 0700);
	char lp[256];
	snprintf(lp, sizeof lp, "%s/packets.log", outdir);
	logf = fopen(lp, "w");
	if (!logf)
		return 1;
	{
		char mp[64];
		snprintf(mp, sizeof mp, "/proc/%d/mem", target);
		memfd = open(mp, O_RDONLY);
		if (memfd < 0)
			fprintf(stderr, "open %s: %s\n", mp, strerror(errno));
	}
	if (getenv("FUXI_DUMP_NOW")) {
		dump_maps(9999, 0);
		fclose(logf);
		return 0;
	}
	signal(SIGINT, sig);
	signal(SIGTERM, sig);
	seize_threads();
	fprintf(stderr, "attached %d threads\n", ntids);
	int status;
	while (!stop_flag) {
		pid_t t = waitpid(-1, &status, __WALL | WNOHANG);
		if (t == 0) {
			usleep(200);
			continue;
		}
		if (t < 0)
			break;
		int s = slot(t, 1);
		if (WIFEXITED(status) || WIFSIGNALED(status)) {
			if (s >= 0)
				tids[s] = 0;
			continue;
		}
		int sg = WSTOPSIG(status), ev = status >> 16, deliver = 0;
		if (sg == (SIGTRAP | 0x80)) {
			struct user_pt_regs regs;
			struct iovec iv = { &regs, sizeof regs };
			if (s >= 0 && ptrace(PTRACE_GETREGSET, t, (void *)NT_PRSTATUS, &iv) == 0) {
				in_sys[s] = !in_sys[s];
				if (in_sys[s] && regs.regs[8] == 64 && (int)regs.regs[0] == gpr_fd)
					on_write(t, regs.regs[1], regs.regs[2]);
			}
		} else if (ev == PTRACE_EVENT_CLONE) {
			unsigned long nt;
			ptrace(PTRACE_GETEVENTMSG, t, 0, &nt);
			if (slot(nt, 0) < 0)
				slot(nt, 1);
		} else if (ev == PTRACE_EVENT_STOP) {
			// 新线程或 INTERRUPT 的停：不转发信号
		} else if (sg != SIGTRAP) {
			deliver = sg;
		}
		ptrace(PTRACE_SYSCALL, t, 0, deliver);
	}
	for (int i = 0; i < ntids; i++)
		if (tids[i]) {
			kill(tids[i], 0);
			ptrace(PTRACE_INTERRUPT, tids[i], 0, 0);
		}
	usleep(100000);
	for (int i = 0; i < ntids; i++)
		if (tids[i]) {
			int st;
			waitpid(tids[i], &st, __WALL | WNOHANG);
			ptrace(PTRACE_DETACH, tids[i], 0, 0);
		}
	fclose(logf);
	return 0;
}
