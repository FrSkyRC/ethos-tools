---
name: ethos-navigate
description: Drive the FrSky Ethos radio firmware in its WASM simulator — boot a radio (X20S, X18, X14...), see its screen as PNG screenshots, and operate it with touch, rotary encoder, hardware keys (SYS/MDL/DISP/RTN/PAGE/ENTER), switches and sticks. Use when asked to navigate Ethos menus, check what a screen or Lua widget/tool looks like, reproduce a UI bug, configure a model or radio setting through the UI, or run a Lua macro in the simulator.
---

# Navigating Ethos in the WASM simulator

`sim_driver.js` keeps an Ethos simulator running behind a local HTTP server.
You act on it with one-shot client commands and look at the result with `screenshot` + Read.

The driver ships with this skill: it is `../../sim_driver.js` relative to this skill's base directory
(the plugin root). Resolve it to an absolute path once, and use that path wherever this page says `"$DRIVER"`.
Shell variables don't persist between Bash calls, so write the absolute path into each command.
Requires Node.js 18+.

## 1. Find a simulator build

Builds are `<BOARD>_<PROTOCOL>.js` + `.wasm` pairs (e.g. `X20S_FCC`). The Ethos VS Code extension caches them in:

```
%APPDATA%\Code\User\globalStorage\bsongis.ethos\cache\        (Git Bash: "$APPDATA/Code/User/globalStorage/bsongis.ethos/cache")
```

List it to see which boards are available. If the user names a board, use that one; otherwise default to `X20S_FCC`.
If nothing is cached, ask the user to run "Ethos: Start" once in VS Code, or to point you at a build.

## 2. Pick the radio storage (root directory)

`--root-directory DIR` mounts a real folder as the radio's storage (`radio.bin`, `models/`, `scripts/`, `audio/`...).
**The simulator writes to it** (settings, RAM backup, models), so:

- To explore or test without side effects, copy the folder to the scratchpad first and mount the copy.
- Mount the user's folder directly only when they want changes kept (e.g. "configure this model").
- With no `--root-directory`, an empty in-memory radio is used (factory state, no models or scripts).

The VS Code extension's per-radio folders are usually `<workspace>/simulator/<BOARD>_<PROTOCOL>` or `<workspace>/simulators/<BOARD>_<PROTOCOL>@<release>`.

## 3. Start the server (background)

Run in the background (Bash `run_in_background: true`):

```bash
node "$DRIVER" serve "<path>/X20S_FCC.js" --root-directory "<radio dir>" --shots-dir "<scratchpad>/shots"
```

Then poll until it answers (boot takes a few seconds):

```bash
for i in $(seq 1 30); do node "$DRIVER" status && break; node -e "setTimeout(()=>{},1000)"; done
```

`status` returns the screen size, switch positions and trims. Use `--port N` on both serve and client to run several radios at once (default 8765).

## 4. The navigation loop

1. `node "$DRIVER" screenshot <scratchpad>/shots/<name>.png`, then **Read** the PNG.
2. Decide one action from what is on screen.
3. Run the action. Every action waits for the screen to settle before returning.
4. Take another screenshot to confirm. Never assume an action worked.

Name screenshots after what they show (`system-menu.png`, `after-ok.png`) so the trail makes sense.

### Commands

| Command | Effect |
|---|---|
| `screenshot [out.png]` | Saves the current screen as a PNG and returns its path and size |
| `tap X Y` | Touch at screen pixel (top-left origin, same coordinates as the screenshot) |
| `longpress X Y` | 1 s hold: context menus, e.g. on a widget or a model |
| `swipe X1 Y1 X2 Y2 [ms]` | Drag: scroll lists, change menu page, adjust sliders |
| `wheel N` | Rotary encoder N detents (+ clockwise / next, − back) |
| `press KEY [holdMs]` | Press and release a key. Use `holdMs` ≥ 1000 for a long press |
| `keydown KEY` / `keyup KEY` | Hold or release a key |
| `switch I V` | Physical switch I to -100 / 0 / 100 (`status` shows the positions) |
| `fswitch I V` | Function switch I pressed (1) / released (0) |
| `analog I V` | Stick / pot / slider I value |
| `trim I V` | Trim button I pressed (1) / released (0) |
| `macro PATH [timeoutMs]` | Runs a Lua macro (e.g. `USER:/macros/x.lua`) to completion |
| `wait MS` | Pause, e.g. for an animation or a timed alert |
| `log [n]` | Last n lines of simulator console output (Lua errors and `print` show up here) |
| `quit` | Stop the simulator. **Always do this when finished** |

### Keys

| Radio button | KEY | What it opens |
|---|---|---|
| SYS | `SYS` | System menu |
| MDL | `MDL` | Model menu |
| DISP | `DISP` | Configure screens |
| RTN | `RTN` | Back / close dialog |
| PAGE | `PAGE` | Next page or tab |
| Encoder press | `ENTER` | Select / edit the focused item |

Any browser key name also works (`Escape`, `PageDown`, `Tab`, letters...). `press` returns `"used": false` if Ethos ignored the key.

## Screen layout (X20 family, 800×480)

- Top bar (y ≈ 0–60): back arrow `<` at about (30, 33), page title, then status icons.
- Home screen bottom bar (y ≈ 448): Home (78), Model ✈ (225), Screens ⊞ (372), System ⚙ (518), clock.
- Menus are 4×2 tile grids: tile centres at x ≈ 148 / 316 / 483 / 650, y ≈ 165 / 330. The dots at y ≈ 75 show the page. Swipe horizontally or press `PAGE` to change page.
- Settings pages are lists of label/value rows about 58 px tall. Tap the value to edit it, and swipe vertically to scroll.

Other boards have different resolutions (X18/Twin X Lite 480×320, X14 640×360...). Check `width`/`height` from `screenshot` and work from the image, not from these numbers.

## Tips

- SYS / MDL / DISP only jump to their menu from the top level. From inside a settings page they are ignored, even though `press` still returns `"used": true`. Press `RTN` until you're back at Home (or a menu grid), then press the menu key.
- Don't chain several actions blindly. If one misses (above), the rest land on the wrong screen. Screenshot between steps unless the path was already verified in this session.
- Booting usually shows a **Checklist warning** dialog (throttle, failsafe). Dismiss it with its **OK** button at the bottom right, or with `RTN`.
- Touch is usually the most direct way in. Use `wheel` + `ENTER` when a target is small or only reachable by focus.
- Lua errors show up in `log`, not on screen. Check `log` when a widget or tool looks blank.
- Don't send input while `macro` runs. It blocks until the macro ends.
- Report what you saw: include the key screenshot paths in your answer so the user can open them.
