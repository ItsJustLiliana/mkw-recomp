// When this PC's server listens again (runtime/sdk/ov/net_listen.h): the address changes of 14/11/2026, when Tailscale
// dropped its address for 4 s and each change restarted the server, replayed second by second with example addresses.
#include <stdio.h>

#include "net_listen.h"

static int failed, passed;

static void check(int ok, const char *what) {
    if (ok) {
        passed++;
    } else {
        failed++;
        printf("FAIL %s\n", what);
    }
}

#define ALL "127.0.0.1,192.168.1.10,100.100.1.2"
#define HOME "127.0.0.1,192.168.1.10"

// How many times net_listen_settled says "listen again" while now is seen from second from to second to (included)
static int restarts(NetListenWatch *w, const char *running, const char *now, int from, int to) {
    int n = 0;
    for (int s = from; s <= to; s++) n += net_listen_settled(w, running, now, (uint64_t)s * 1000);
    return n;
}

int main(void) {
    check(!net_listen_added("a,b", "a") && !net_listen_added("a,b", "b,a"), "a list without a new address");
    check(net_listen_added("a", "a,b") && net_listen_added("10.0.0.1", "10.0.0.11") &&
              net_listen_added("10.0.0.11", "10.0.0.1"),
          "a new address, not taken for a prefix of another");

    NetListenWatch w = {0};
    // 14/11: all three, Tailscale's gone at 17 s, back at 21 s: nothing to do
    int n = restarts(&w, ALL, ALL, 0, 16) + restarts(&w, ALL, HOME, 17, 20) + restarts(&w, ALL, ALL, 21, 40);
    check(n == 0, "Tailscale gone for 4 s: the server keeps listening");
    // gone for good: listen again 30 s after it went, not before
    n = restarts(&w, ALL, HOME, 41, 70);
    check(n == 0 && net_listen_settled(&w, ALL, HOME, 71000), "Tailscale gone for 30 s: listen again");

    // Tailscale up after the server started: 5 s
    NetListenWatch up = {0};
    check(restarts(&up, HOME, ALL, 0, 4) == 0 && net_listen_settled(&up, HOME, ALL, 5000), "Tailscale up for 5 s");
    // the box gave another address: an address added, 5 s
    NetListenWatch box = {0};
    check(restarts(&box, HOME, "127.0.0.1,192.168.1.11", 0, 4) == 0 &&
              net_listen_settled(&box, HOME, "127.0.0.1,192.168.1.11", 5000),
          "another address from the box: 5 s");
    // a list that keeps changing never settles
    NetListenWatch flap = {0};
    n = 0;
    for (int s = 0; s < 60; s++) n += net_listen_settled(&flap, HOME, s % 3 ? ALL : "127.0.0.1", (uint64_t)s * 1000);
    check(n == 0, "addresses that change every few seconds: no restart");

    printf("%d passed, %d failed\n", passed, failed);
    return failed != 0;
}
