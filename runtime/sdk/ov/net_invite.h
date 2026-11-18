// Server routing invites for private networks: no directory service, account secret or room state is encoded.
// The checksum catches typing mistakes; it is not authentication. Access is controlled by the VPN and firewall.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static inline int net_invite_local(uint32_t ip) {
    return (ip >> 24) == 10 || (ip >> 20) == 0xac1 || (ip >> 16) == 0xc0a8 ||
           (ip >> 22) == 0x191 || (ip >> 24) == 127;
}

static inline unsigned net_invite_check(uint32_t ip, unsigned port) {
    return (ip ^ (ip >> 16) ^ port ^ 0x4d4b) & 0xffff;
}

static inline int net_invite_make(uint32_t ip, unsigned port, char out[32]) {
    out[0] = 0;
    if (!net_invite_local(ip) || !port || port > 65533) return 0;
    snprintf(out, 32, "MKW1-%08X-%04X-%04X", (unsigned)ip, port, net_invite_check(ip, port));
    return 1;
}

static inline int net_invite_hex(const char *s, int count, uint32_t *value) {
    *value = 0;
    for (int i = 0; i < count; i++) {
        unsigned char c = (unsigned char)s[i];
        unsigned d = c >= '0' && c <= '9' ? c - '0' : c >= 'A' && c <= 'F' ? c - 'A' + 10 :
                     c >= 'a' && c <= 'f' ? c - 'a' + 10 : 16;
        if (d > 15) return 0;
        *value = (*value << 4) | d;
    }
    return 1;
}

static inline int net_invite_parse(const char *text, char endpoint[32]) {
    uint32_t ip, port, check;
    endpoint[0] = 0;
    if (!text || strlen(text) != 23 || strncmp(text, "MKW1-", 5) || text[13] != '-' || text[18] != '-' ||
        !net_invite_hex(text + 5, 8, &ip) || !net_invite_hex(text + 14, 4, &port) ||
        !net_invite_hex(text + 19, 4, &check) || !net_invite_local(ip) || !port || port > 65533 ||
        check != net_invite_check(ip, port)) return 0;
    snprintf(endpoint, 32, "%u.%u.%u.%u:%u", ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255, ip & 255, port);
    return 1;
}

// F1 also accepts a public server address. This checks syntax only; DNS and probing run on its worker.
static inline int net_join_endpoint(const char *text, char endpoint[160]) {
    endpoint[0] = 0;
    if (!text || !*text || strlen(text) >= 160) return 0;
    if (!strncmp(text, "MKW1-", 5)) return net_invite_parse(text, endpoint);
    const char *colon = strchr(text, ':');
    size_t host_len = colon ? (size_t)(colon - text) : strlen(text);
    if (!host_len || host_len > 253 || text[0] == '.' || text[host_len - 1] == '.') return 0;
    for (size_t i = 0; i < host_len; i++) {
        unsigned char c = (unsigned char)text[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '.'))
            return 0;
    }
    if (colon) {
        unsigned port = 0;
        if (!colon[1]) return 0;
        for (const char *p = colon + 1; *p; p++) {
            if (*p < '0' || *p > '9' || port > 6553) return 0;
            port = port * 10 + (unsigned)(*p - '0');
        }
        if (!port || port > 65533) return 0;
    }
    memcpy(endpoint, text, strlen(text) + 1);
    return 1;
}
