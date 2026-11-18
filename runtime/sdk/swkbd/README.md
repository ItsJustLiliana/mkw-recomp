# runtime/sdk/swkbd

Software keyboard.

| File | What it is |
|---|---|
| `swkbd.c`, `swkbd.h`, `swkbd_sdl.c` | The SDK's software keyboard and modal request/reply seam to the ImGui game overlay: actual typing, numeric keypad, confirm/cancel, a sleeping guest caller and independent redraws if the game stops presenting; only an explicit environment variable supplies scripted answers |
