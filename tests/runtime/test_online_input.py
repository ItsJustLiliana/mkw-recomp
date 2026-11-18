"""Exercise the real SDL F1 adapter, event queue and Online field in headless Dear ImGui.

Why: the launcher uses SDL's ImGui backend, so launcher screenshots missed missing in-game text events and polling
that disabled the invite field. No game, GPU, server or real keyboard input is used.
Usage: uv run pytest tests/test_online_input.py (bundled Windows dependencies, or SDL3 via pkg-config).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_f1_typing_survives_polling(tmp_path: Path) -> None:
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang++.exe"
    cc = str(bundled) if os.name == "nt" and bundled.exists() else shutil.which("clang++") or shutil.which("c++")
    if not cc:
        pytest.skip("no C++ compiler")
    deps = ROOT / "build/win/deps"
    if os.name == "nt":
        if not (deps / "include/SDL3/SDL.h").exists():
            pytest.skip("run tools/build/windeps.py first")
        sdl = [f"-I{deps / 'include'}", f"-L{deps / 'lib'}", "-lSDL3"]
    else:
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config missing")
        result = subprocess.run(["pkg-config", "--cflags", "--libs", "sdl3"], capture_output=True, text=True)
        if result.returncode:
            pytest.skip("SDL3 development files missing")
        sdl = result.stdout.split()
    menu = (ROOT / "runtime/ui/menu/menu.cpp").read_text()
    events = menu[menu.index("enum { EV_KEY,"):menu.index("static int text_filter(")]
    keyboard_ui = menu[menu.index("static int text_filter("):menu.index("// ---- gamepad navigation:")]
    keyboard = (ROOT / "runtime/sdk/swkbd/swkbd.c").read_text()
    conversions = keyboard[keyboard.index("size_t swkbd_utf16_to_utf8("):keyboard.index("// Drop disallowed")]
    code = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <chrono>
#include <SDL3/SDL.h>
#include "imgui.h"
#include "imgui_internal.h"
extern "C" {
#include "platform/platform.h"
#include "input_sdl.h"
#include "ui/menu/menu.h"
#include "net_online.h"
#include "ui_text.h"
#include "swkbd.h"
static int opened = 1;
int menu_is_open(void) { return opened || text_entry_active(); }
void menu_toggle(void) {
    if (text_entry_active()) { SwkbdRequest r; text_entry_finish(text_entry_snapshot(&r), NULL); }
    else opened = !opened;
}
void menu_perf_cycle(void) {}
double gfx_window_sdl_density(void) { return 1; }
static std::mutex init_lock;
static std::mutex &mutex_of(PlatMutex *m) {
    std::lock_guard<std::mutex> lock(init_lock);
    if (!m->p) m->p = new std::mutex;
    return *(std::mutex *)m->p;
}
void plat_mutex_lock(PlatMutex *m) { mutex_of(m).lock(); }
void plat_mutex_unlock(PlatMutex *m) { mutex_of(m).unlock(); }
static std::condition_variable cv;
void plat_cond_wait(PlatCond *, PlatMutex *m) {
    std::unique_lock<std::mutex> lock(mutex_of(m), std::adopt_lock);
    cv.wait(lock);
    lock.release();
}
void plat_cond_signal(PlatCond *) { cv.notify_one(); }
int gfx_text_entry_start(unsigned) { return 1; } // headless test supplies the present frames itself
const char *cfg_get(const char *) { return ""; }
void cfg_set(const char *, const char *) {}
int cfg_save(void) { return 1; }
int net_enabled(void) { return 0; }
int net_host_enabled(void) { return 0; }
void net_addrs(NetAddrs *a) { memset(a, 0, sizeof *a); }
const char *net_network_address(const NetAddrs *) { return ""; }
int net_host_start(char *, size_t) { return -1; }
void net_host_stop(void) {}
void net_clients_start(void) {}
void net_party_snapshot(NetParty *out) { memset(out, 0, sizeof *out); }
void net_format_code(const char *, char out[18]) { out[0] = 0; }
NetHostState net_host_state(char *, size_t) { return NET_HOST_OFF; }
int net_host_firewall(void) { return -1; }
const char *net_probe(const char *, char *, size_t) { return "unavailable"; }
uint8_t *net_server_ca(const char *, size_t *) { return NULL; }
const char *net_fetch_ca(const char *, char *, size_t) { return "unavailable"; }
const char *menu_text(UiTextId id) {
    static char labels[UI_TEXT_COUNT][24];
    snprintf(labels[id], sizeof labels[id], "Label%d", (int)id);
    return labels[id];
}
}
extern "C" {
''' + conversions + r'''
#include "sdk/swkbd/swkbd_sdl.c"
}
''' + events + keyboard_ui + r'''
#include "ui/menu/online_tab.cpp"
static void frame() {
    drain_events(ImGui::GetIO());
    ImGui::NewFrame();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(ImVec2(1200, 700));
    ImGui::Begin("test");
    online_tab(1, 30);
    ImGui::End();
    if (text_entry_active()) layout_text_entry(1200, 700, 1);
    ImGui::Render();
}
static void text(const char *value) {
    SDL_Event e = {};
    e.type = SDL_EVENT_TEXT_INPUT;
    e.text.text = value;
    menu_sdl_event(&e);
}
int main() {
    SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "dummy");
    assert(SDL_Init(SDL_INIT_VIDEO));
    SDL_Window *window = SDL_CreateWindow("test", 100, 100, SDL_WINDOW_HIDDEN);
    assert(window);
    menu_sdl_idle(window);
    assert(SDL_TextInputActive(window));
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = NULL;
    io.DisplaySize = ImVec2(1280, 720);
    io.DeltaTime = 1.0f / 60;
    io.ConfigInputTrickleEventQueue = false;
    unsigned char *pixels; int width, height;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &width, &height);
    initialized = true;
    active.store(REFRESH);  // a slow in-flight status probe, with no network thread
    frame();
    ImGuiWindow *child = NULL;
    for (auto *w : GImGui->Windows) if (strstr(w->Name, "##online_controls")) child = w;
    assert(child);
    ImGuiID field = child->GetID("##invite");
    ImGui::FocusWindow(child);
    ImGui::SetFocusID(field, child);
    ImGui::ActivateItemByID(field);
    frame();
    assert(GImGui->ActiveId == field);
    text("MKW1-E");
    frame();
    assert(!strcmp(join_code, "MKW1-E"));
    assert(GImGui->ActiveId == field);
    // Complete and start more background polls while typing: the field must remain enabled and focused.
    for (int i = 0; i < 3; i++) {
        active.store(IDLE);
        frame();
        active.store(REFRESH);
        text("A");
        frame();
        assert(GImGui->ActiveId == field);
    }
    assert(!strcmp(join_code, "MKW1-EAAA"));
    SDL_Event key = {};
    key.type = SDL_EVENT_KEY_DOWN;
    key.key.scancode = SDL_SCANCODE_A;
    key.key.mod = SDL_KMOD_CTRL;
    menu_sdl_event(&key);
    frame();
    assert(io.KeyCtrl && ImGui::IsKeyDown(ImGuiKey_A));
    key.type = SDL_EVENT_KEY_UP;
    key.key.mod = 0;
    menu_sdl_event(&key);
    text("\xc3\xa9");
    frame();
    assert(!strcmp(join_code, "\xc3\xa9")); // Ctrl+A selected all; UTF-8 input replaces it
    ImGui::GetPlatformIO().Platform_GetClipboardTextFn = [](ImGuiContext *) { return "MKW1-64400001-20FB-29F5"; };
    key.type = SDL_EVENT_KEY_DOWN;
    key.key.scancode = SDL_SCANCODE_A;
    key.key.mod = SDL_KMOD_CTRL;
    menu_sdl_event(&key);
    frame();
    key.type = SDL_EVENT_KEY_UP;
    menu_sdl_event(&key);
    frame();
    key.type = SDL_EVENT_KEY_DOWN;
    key.key.scancode = SDL_SCANCODE_V;
    menu_sdl_event(&key);
    frame();
    assert(!strcmp(join_code, "MKW1-64400001-20FB-29F5"));
    key.type = SDL_EVENT_KEY_UP;
    key.key.mod = 0;
    menu_sdl_event(&key);
    frame();
    active.store(REFRESH);
    submit(JOIN, "10.0.0.2:8443");
    assert(pending.action == JOIN && !strcmp(pending.endpoint, "10.0.0.2:8443"));
    assert(active.load() == REFRESH); // no conflicting worker or dropped click
    // The game's room-code request waits for real input even with F1 closed; no ini answer is needed.
    opened = 0;
    SwkbdRequest request = {};
    request.numeric = 1;
    request.min_len = request.max_len = 6;
    uint16_t answer[KB_LIMIT + 1] = {};
    size_t answer_len = 0;
    std::atomic<int> result{-1};
    std::thread caller([&] { result = text_entry_run(&request, answer, KB_LIMIT, &answer_len); });
    for (int i = 0; i < 1000 && !text_entry_active(); i++) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(text_entry_active() && result == -1);
    menu_sdl_idle(window);
    assert(SDL_TextInputActive(window));
    frame(); frame();
    text("123456");
    frame();
    assert(result == -1);
    key.type = SDL_EVENT_KEY_DOWN;
    key.key.scancode = SDL_SCANCODE_RETURN;
    menu_sdl_event(&key);
    frame();
    caller.join();
    assert(result == 1 && answer_len == 6 && answer[0] == '1' && answer[5] == '6');
    result = -1;
    std::thread cancelled([&] { result = text_entry_run(&request, answer, KB_LIMIT, &answer_len); });
    for (int i = 0; i < 1000 && !text_entry_active(); i++) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(text_entry_active());
    SwkbdRequest snapshot;
    unsigned id = text_entry_snapshot(&snapshot);
    text_entry_finish(id - 1, "999999");  // a stale dialog cannot answer a newer request
    assert(text_entry_active());
    key.key.scancode = SDL_SCANCODE_F1;
    menu_sdl_event(&key);
    cancelled.join();
    assert(result == 0 && answer_len == 0);
    menu_sdl_idle(window);
    assert(!SDL_TextInputActive(window));
    text("ignored");
    assert(ev_n == 0);
    pending.action = IDLE;
    active.store(IDLE);
    ImGui::DestroyContext();
    SDL_DestroyWindow(window);
    SDL_Quit();
}
'''
    source, exe = tmp_path / "input.cpp", tmp_path / "input.exe"
    source.write_text(code)
    includes = sorted({ROOT / "runtime", *(p.parent for p in (ROOT / "runtime").rglob("*.h"))})
    imgui = ROOT / "runtime/third_party/imgui"
    files = [source, ROOT / "runtime/ui/menu/menu_sdl.cpp",
             *(imgui / name for name in ("imgui.cpp", "imgui_draw.cpp", "imgui_widgets.cpp", "imgui_tables.cpp"))]
    subprocess.run([cc, "-std=c++17", "-O0", *[f"-I{p}" for p in includes], *map(str, files), *sdl,
                    "-o", str(exe)], check=True)
    env = dict(os.environ)
    if os.name == "nt":
        env["PATH"] = os.pathsep.join([str(deps / "bin"), str(Path(cc).parent), env.get("PATH", "")])
    subprocess.run([str(exe)], env=env, check=True, timeout=20)
