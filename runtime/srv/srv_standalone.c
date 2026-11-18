// Linux server-only entry point. Build: uv run tools/build/server.py; no game, SDL or Vulkan is linked.
// The ordinary executable still uses core/boot/main.c and the full platform layer.
#ifdef MKW_SERVER_STANDALONE
#include <errno.h>
#include <sys/random.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <string.h>
#include "platform/platform.h"
#include "srv.h"

uint32_t plat_process_id(void) { return (uint32_t)getpid(); }
uint32_t plat_thread_id(void) { return (uint32_t)syscall(SYS_gettid); }
int plat_random(void *out, size_t n) {
    unsigned char *p = out;
    while (n) {
        ssize_t got = getrandom(p, n, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) return 0;
        p += got;
        n -= (size_t)got;
    }
    return 1;
}
int main(int argc, char **argv) {
    int first = argc > 1 && !strcmp(argv[1], "--server") ? 2 : 1;
    return srv_main(argc - first, argv + first);
}
#endif
