// F1 private-server controls, using the server and client integration from PRs #353/#354 (net_online.h).
// Unlike the launcher page, this runs during gameplay: even interface enumeration and port probes must be off
// the present/replay path. One detached worker handles an action and publishes a bounded snapshot under a lock.
#include <atomic>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "imgui.h"
#include "online_tab.h"
extern "C" {
#include "cfg.h"
#include "net_online.h"
#include "net_invite.h"
#include "platform/platform.h"
#include "ui_text.h"
}

enum { IDLE = -1, REFRESH, HOST, JOIN, STOP, OFF, FIREWALL };
struct Snapshot {
    NetHostState host;
    char detail[200], invite[32], server[160], status[300];
    int enabled, restart;
};
static PlatMutex lock = PLAT_MUTEX_INIT;
static Snapshot snapshot;
static std::atomic<int> active{IDLE};
static bool initialized;
static char join_code[160];
static int network;
static const char *const networks[] = {"auto", "home", "tailscale"};
struct Task { int action; char endpoint[160]; int network; };
static Task pending{IDLE, "", 0};   // UI thread only: a click during a status probe must not disappear

static int SDLCALL work(void *arg) {
    Task task = *(Task *)arg;
    SDL_free(arg);
    Snapshot next;
    plat_mutex_lock(&lock);
    next = snapshot;
    plat_mutex_unlock(&lock);
    char err[240] = "";
    if (task.action == HOST) {
        NetAddrs addresses;
        cfg_set("MKW_NETWORK", networks[task.network]);
        net_addrs(&addresses);
        const char *ip = net_network_address(&addresses);
        if (!*ip) {
            snprintf(err, sizeof err, "%s", menu_text(UI_F1_NO_ADDRESS));
        } else {
            snprintf(next.server, sizeof next.server, "%s:%d", ip, NET_HOST_PORT);
            cfg_set("MKW_ONLINE_SERVER", next.server);
            cfg_set("MKW_ONLINE", "1");
            cfg_set("MKW_SERVER_HOST", "1");
            if (!net_host_start(err, sizeof err)) next.restart = 1;
            cfg_save();
        }
    } else if (task.action == JOIN) {
        // Keep an existing server alive until the new endpoint has answered. A failed join preserves settings.
        if (net_host_enabled() && !strcmp(task.endpoint, next.server)) {
            snprintf(next.status, sizeof next.status, "%s", menu_text(UI_F1_OWN_INVITE));
            plat_mutex_lock(&lock);
            snapshot = next;
            plat_mutex_unlock(&lock);
            active.store(IDLE);
            return 0;
        }
        const char *why = net_probe(task.endpoint, err, sizeof err);
        bool ok = !*why;
        if (ok) {
            size_t size;
            uint8_t *ca = net_server_ca(task.endpoint, &size);
            if (!ca) ok = !*net_fetch_ca(task.endpoint, err, sizeof err);
            free(ca);
        }
        if (ok) {
            net_host_stop();
            cfg_set("MKW_ONLINE_SERVER", task.endpoint);
            cfg_set("MKW_ONLINE", "1");
            cfg_set("MKW_SERVER_HOST", "0");
            cfg_save();
            next.restart = 1;
        } else if (*why && why != err) snprintf(err, sizeof err, "%s", why);
    } else if (task.action == STOP || task.action == OFF) {
        net_host_stop();
        cfg_set("MKW_SERVER_HOST", "0");
        if (task.action == OFF) cfg_set("MKW_ONLINE", "0");
        cfg_save();
        next.restart = 1;
    } else if (task.action == FIREWALL) {
        if (net_host_firewall()) snprintf(err, sizeof err, "%s", menu_text(UI_F1_FIREWALL_FAILED));
    }
    if (task.action != REFRESH)
        snprintf(next.status, sizeof next.status, "%s", *err ? err : menu_text(UI_F1_SAVED));
    next.host = net_host_state(next.detail, sizeof next.detail);
    next.enabled = net_enabled();
    const char *server = cfg_get("MKW_ONLINE_SERVER");
    snprintf(next.server, sizeof next.server, "%s", server ? server : "");
    next.invite[0] = 0;
    if ((next.host == NET_HOST_RUNNING || next.host == NET_HOST_OTHER) && net_host_enabled()) {
        unsigned a, b, c, d, port;
        if (sscanf(next.server, "%u.%u.%u.%u:%u", &a, &b, &c, &d, &port) == 5 &&
            a < 256 && b < 256 && c < 256 && d < 256)
            net_invite_make((a << 24) | (b << 16) | (c << 8) | d, port, next.invite);
    }
    plat_mutex_lock(&lock);
    snapshot = next;
    plat_mutex_unlock(&lock);
    active.store(IDLE);
    return 0;
}

static void dispatch(const Task &request) {
    int expected = IDLE;
    if (!active.compare_exchange_strong(expected, request.action)) {
        if (request.action != REFRESH) pending = request;
        return;
    }
    Task *task = (Task *)SDL_calloc(1, sizeof *task);
    if (task) {
        *task = request;
        if (SDL_Thread *thread = SDL_CreateThread(work, "online controls", task)) {
            SDL_DetachThread(thread);
            return;
        }
        SDL_free(task);
    }
    active.store(IDLE);
    plat_mutex_lock(&lock);
    snprintf(snapshot.status, sizeof snapshot.status, "%s", menu_text(UI_F1_WORKER_FAILED));
    plat_mutex_unlock(&lock);
}

static void submit(int action, const char *endpoint = "") {
    Task request{action, "", network};
    snprintf(request.endpoint, sizeof request.endpoint, "%s", endpoint);
    dispatch(request);
}

void online_tab(float scale, float bottom) {
    if (!initialized) {
        initialized = true;
        net_clients_start();
        const char *n = cfg_get("MKW_NETWORK");
        for (int i = 0; n && i < 3; i++) if (!strcmp(n, networks[i])) network = i;
        submit(REFRESH);
    }
    static uint64_t polled;
    uint64_t now = SDL_GetTicks();
    if (pending.action != IDLE && active.load() == IDLE) {
        Task request = pending;
        pending.action = IDLE;
        dispatch(request);
    }
    if (now - polled > 2000 && active.load() == IDLE) {
        polled = now;
        submit(REFRESH);
    }
    Snapshot view;
    plat_mutex_lock(&lock);
    view = snapshot;
    plat_mutex_unlock(&lock);
    ImGui::BeginChild("##online_controls", ImVec2(0, -bottom), ImGuiChildFlags_NavFlattened);
    ImGui::TextWrapped("%s", menu_text(UI_F1_EXPERIMENTAL));
    ImGui::TextWrapped("%s", menu_text(UI_F1_INSTRUCTIONS));
    ImGui::SeparatorText(menu_text(UI_ONLINE_NETWORK));
    const UiTextId names[] = {UI_ONLINE_NET_AUTO, UI_ONLINE_NET_HOME, UI_ONLINE_NET_TS};
    // Status polling never changes widget enablement or focus. Only an explicit action blocks other actions.
    bool changing = active.load() > REFRESH || pending.action != IDLE;
    bool running = view.host == NET_HOST_RUNNING || view.host == NET_HOST_STARTING || view.host == NET_HOST_OTHER;
    ImGui::BeginDisabled(changing || running);
    for (int i = 0; i < 3; i++) {
        if (ImGui::RadioButton(menu_text(names[i]), network == i)) network = i;
        if (i != 2) ImGui::SameLine();
    }
    if (ImGui::Button(menu_text(UI_ONLINE_HOST_START))) submit(HOST);
    ImGui::EndDisabled();
    const UiTextId host_names[] = {UI_ONLINE_HOST_OFF, UI_ONLINE_HOST_STARTING, UI_F1_RUNNING,
                                   UI_F1_FAILED, UI_ONLINE_HOST_OTHER};
    ImGui::TextWrapped("%s", menu_text(host_names[view.host]));
    if (*view.detail) ImGui::TextWrapped("%s", view.detail);
    if (*view.invite) {
        ImGui::TextUnformatted(menu_text(UI_F1_INVITE));
        ImGui::TextUnformatted(view.invite);
        if (ImGui::Button(menu_text(UI_ONLINE_COPY))) ImGui::SetClipboardText(view.invite);
    }
    ImGui::BeginDisabled(changing || !running);
    if (ImGui::Button(menu_text(UI_ONLINE_HOST_STOP))) submit(STOP);
    ImGui::EndDisabled();
    ImGui::BeginDisabled(changing);
    if (ImGui::Button(menu_text(UI_ONLINE_HOST_FIREWALL))) submit(FIREWALL);
    ImGui::EndDisabled();
    ImGui::TextWrapped("%s", menu_text(UI_ONLINE_HOST_FIREWALL_DESC));
    ImGui::SeparatorText(menu_text(UI_ONLINE_SERVER));
    ImGui::SetNextItemWidth(430 * scale);
    ImGui::InputTextWithHint("##invite", "server.example:18443 / MKW1-...", join_code, sizeof join_code,
                             ImGuiInputTextFlags_CharsNoBlank);
    char endpoint[160];
    bool valid = net_join_endpoint(join_code, endpoint) != 0;
    ImGui::BeginDisabled(changing || !valid);
    if (ImGui::Button(menu_text(UI_F1_CONNECT))) submit(JOIN, endpoint);
    ImGui::EndDisabled();
    if (*join_code && !valid) ImGui::TextWrapped("%s", menu_text(UI_ONLINE_SERVER_HINT));
    ImGui::BeginDisabled(changing);
    if (ImGui::Button(menu_text(UI_F1_OFF))) submit(OFF);
    ImGui::EndDisabled();
    if (*view.server) ImGui::TextWrapped("%s: %s", menu_text(UI_ONLINE_SERVER), view.server);
    if (view.enabled) {
        NetParty party;
        net_party_snapshot(&party);
        if (strcmp(party.server, view.server) || !party.reached) {
            ImGui::TextWrapped("%s", *party.error ? party.error : menu_text(UI_F1_CLIENTS_WAIT));
        } else if (*party.error) {
            ImGui::TextWrapped("%s", party.error);
        } else {
            char title[120];
            snprintf(title, sizeof title, menu_text(UI_F1_CLIENTS), party.nclients);
            ImGui::SeparatorText(title);
            for (int i = 0; i < party.nclients; i++) {
                char code[18];
                net_format_code(party.clients[i].code, code);
                ImGui::TextWrapped("%s%s%s (%s)", *party.clients[i].name ? party.clients[i].name : code,
                                   *party.clients[i].name ? " - " : "", *party.clients[i].name ? code : "",
                                   menu_text(party.clients[i].online ? UI_F1_CLIENT_GAME : UI_F1_CLIENT_ONLY));
            }
            if (*party.room) {
                snprintf(title, sizeof title, menu_text(UI_F1_PARTY_ROOM), party.room);
                ImGui::SeparatorText(title);
                for (int i = 0; i < party.nmembers; i++) {
                    char code[18];
                    net_format_code(party.members[i].code, code);
                    ImGui::TextWrapped("%s", *party.members[i].name ? party.members[i].name : code);
                }
            } else ImGui::TextWrapped("%s", menu_text(UI_F1_PARTY_NONE));
        }
    }
    if (*view.status) ImGui::TextWrapped("%s", view.status);
    if (view.restart) ImGui::TextWrapped("%s", menu_text(UI_F1_RESTART));
    if (changing) ImGui::TextUnformatted("...");
    ImGui::EndChild();
}
