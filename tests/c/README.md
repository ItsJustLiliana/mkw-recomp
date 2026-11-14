# tests/c

C sources the Python tests next door build with the compiler at hand and run.

| File | Built by | What it checks |
|---|---|---|
| `t_spirv_images.c` | `test_spirv_images.py` | Runs the actual integer-image shader patcher on synthetic modules, verifies input preservation and the failure-result contract |
| `t_switch2.c` | `test_switch2.py` | The Switch 2 controllers' protocol (`runtime/input/switch2_proto.h`): from seedds/mkw-neo (MIT), with reports and replies recorded from a left and a right Joy-Con 2 |
| `t_game_dir.c` | `test_game_dir.py`, `test_game_dir_windows.py` | Settings, installation discovery and log-folder discovery with real files and single-thread platform stubs; Windows uses UTF-16 file APIs and UTF-8 conversion through `wmain`; no game, SDL or GPU |
| `t_adpcm.c` | `test_adpcm.py` | DSP-ADPCM voices: `audio_mixer.c`'s voice decoding (`voice.inc`, cut out by the test) against an exact decode of the same random buffers, five sample rates, seven pitches up to 3.9 (plus a high-rate case at 192 kHz and pitch 64), with and without contexts; prints output samples, wrong samples and decodes |
| `t_net_listen.c` | `test_net_listen.py` | When this PC's server listens again (`runtime/sdk/ov/net_listen.h`): the address changes of 14/11/2026 (Tailscale's address gone for 4 s) replayed second by second; only a change that lasts (an address added 5 s, one gone 30 s) restarts it |
| `t_srv.c` | `test_srv.py` | The online server's protobuf reader and writer (`runtime/srv/srv_pb.c`) on the encoding guide's examples and malformed input, its STUN reply (`runtime/srv/srv_udp.c`) on RFC 5769's sample addresses and its NAT check on the protocol description's example, the value tree and field paths (`srv_value.c`), Gamesync writes (`srv_docs.c`) and the race referee (`srv_referee.c`, with a predictable `srv_random`) |
