// The game's modal text request, shared with the in-game ImGui overlay. The calling guest thread has already
// released its virtual core (swkbd.c); it sleeps on a condition variable while window/present threads handle input.
#include <string.h>

#include "cfg.h"
#include "swkbd.h"
#include "platform/platform.h"

static PlatMutex entry_lock = PLAT_MUTEX_INIT;
static PlatCond entry_done = PLAT_COND_INIT;
static SwkbdRequest entry;
static unsigned generation;
static int entry_pending, confirmed;
static uint16_t answer[KB_LIMIT + 1];
static size_t answer_len;

int text_entry_active(void) { return __atomic_load_n(&entry_pending, __ATOMIC_ACQUIRE); }

unsigned text_entry_snapshot(SwkbdRequest *req) {
    if (!text_entry_active()) return 0;
    plat_mutex_lock(&entry_lock);
    unsigned id = entry_pending ? generation : 0;
    if (id) *req = entry;
    plat_mutex_unlock(&entry_lock);
    return id;
}

void text_entry_finish(unsigned id, const char *utf8) {
    plat_mutex_lock(&entry_lock);
    if (entry_pending && generation == id) {
        answer_len = utf8 ? swkbd_utf8_to_utf16(utf8, answer, KB_LIMIT) : 0;
        confirmed = utf8 != NULL;
        __atomic_store_n(&entry_pending, 0, __ATOMIC_RELEASE);
        plat_cond_signal(&entry_done);
    }
    plat_mutex_unlock(&entry_lock);
}

int text_entry_run(const SwkbdRequest *req, uint16_t *out, size_t cap, size_t *n) {
    const char *test = cfg_get("MKW_SWKBD_TEST");
    if (test && !strcmp(test, "cancel")) { *n = 0; return 0; }
    if (test && !strcmp(test, "ok")) {
        size_t k = 0;
        while (k < cap && k < KB_LIMIT && req->initial[k]) k++;
        memcpy(out, req->initial, k * 2);
        *n = k;
        return 1;
    }
    plat_mutex_lock(&entry_lock);
    entry = *req;
    if (++generation == 0) ++generation;
    confirmed = 0;
    __atomic_store_n(&entry_pending, 1, __ATOMIC_RELEASE);
    if (!gfx_text_entry_start(generation)) {
        __atomic_store_n(&entry_pending, 0, __ATOMIC_RELEASE);
        answer_len = 0;
    }
    while (entry_pending) plat_cond_wait(&entry_done, &entry_lock);
    *n = answer_len < cap ? answer_len : cap;
    memcpy(out, answer, *n * 2);
    int ok = confirmed;
    plat_mutex_unlock(&entry_lock);
    return ok;
}
