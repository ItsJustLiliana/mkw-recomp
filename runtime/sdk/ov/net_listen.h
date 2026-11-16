// Integrated from Gael50/mkw-recomp @ 918bbd6523729cd18636c051a1e6e62101fd155f (MIT), PR #354.
// When this PC's server should listen again (net_host.c net_host_listen_changed): the addresses of the network choice
// differ from the ones it listens on, and for good. An address added counts once it has stayed NET_LISTEN_ADD_MS, one
// gone once it has stayed gone NET_LISTEN_GONE_MS. Tailscale's adapter drops its address for seconds while it
// reconnects (14/11/2026: 3 addresses, 2 seventeen seconds later, 3 again four seconds after): each change
// restarted the server, which ended every player's session, and moved this PC's own game from Tailscale's address
// to the box's.
// No platform code: tests/c/t_net_listen.c builds it alone.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { NET_LISTEN_ADD_MS = 5000, NET_LISTEN_GONE_MS = 30000 };

typedef struct {
    char seen[128];   // the address list that differs from the server's, as last seen
    uint64_t since;   // when it was first seen
} NetListenWatch;

// 1 when "a,b,c" list now has an address that list running has not
static inline int net_listen_added(const char *running, const char *now) {
    for (const char *p = now; *p;) {
        size_t n = strcspn(p, ",");
        int found = 0;
        for (const char *q = running; *q && !found;) {
            size_t m = strcspn(q, ",");
            found = m == n && !memcmp(p, q, n);
            q += m + (q[m] == ',');
        }
        if (n && !found) return 1;
        p += n + (p[n] == ',');
    }
    return 0;
}

// Called about once a second at time t (ms): 1 when the server listening on running should listen on now instead
static inline int net_listen_settled(NetListenWatch *w, const char *running, const char *now, uint64_t t) {
    if (!strcmp(running, now)) {   // back as it was, or never changed
        w->seen[0] = 0;
        return 0;
    }
    if (strcmp(w->seen, now)) {   // another list: its clock starts
        snprintf(w->seen, sizeof w->seen, "%s", now);
        w->since = t;
        return 0;
    }
    return t - w->since >= (net_listen_added(running, now) ? NET_LISTEN_ADD_MS : NET_LISTEN_GONE_MS);
}
