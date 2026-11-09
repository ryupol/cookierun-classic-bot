# CookieRun Classic Bot

Bot that plays CookieRun Classic for you: taps through menus, runs games back to back, sends friend lives. Works via ADB + OpenCV template matching. **Screen must be 1280×720.**

## First time? Do this (~20 min, macOS + BlueStacks)

1. Install ADB: `brew install android-platform-tools`
2. Install [BlueStacks Air](https://www.bluestacks.com) (Apple Silicon), launch it
3. BlueStacks Settings → Display → set **1280×720**; Settings → Advanced → turn on **Android Debug Bridge**
4. Install CookieRun Classic inside BlueStacks, open it once
5. Connect and run:
   ```bash
   adb connect 127.0.0.1:5555
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python3 main.py
   ```
6. Answer the option prompts. Stop anytime with `Ctrl+C`.

Works. If `adb devices` shows a different port, edit `DEVICE_PORT` in `config.py`.

## What it does

- Plays the full game loop: menu → purchase → run → relay → rewards → repeat
- Optional power-ups each run: Fast Start, Cookie Relay, one of 11 Random Boosts, 7 opening jumps
- Sends + receives all friend lives from the mailbox every 3–6 h, one click
- Restarts the app every 1.5–3 h and solves the anti-bot card challenge to stay under the radar
- Texts you on Telegram if it crashes or gets stuck

## Control it from your phone (Telegram, ~10 min setup)

Buttons, not typed commands. New users start here.

1. Message [@BotFather](https://t.me/BotFather) → create bot → copy token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your numeric id
3. Copy `.env.example` to `.env`, fill in:
   ```
   TELEGRAM_BOT_TOKEN=123456:abc-your-token
   TELEGRAM_ALLOWED_USER_IDS=111111111
   ```
   Empty allowlist = controller refuses to start (a leaked token can't spawn processes on your Mac)
4. `brew install tmux`, then `./cookiebot.sh start`
5. In Telegram, send `/start` to your bot

### Daily use (3 things to know)

1. `/start` → tap options on the button board (highlighted = ON) → tap **▶️ Start**
2. `/menu` → buttons for status, logs, screenshot, pause, stop
3. `/stop` → bot exits, game closes

The board's Random Boost button expands to 11 named boost buttons — tap one to pick it. Old boards lose their buttons when a new one appears, so only the latest board is tappable.

<details><summary>Typed flags (power users)</summary>

`/start fs=y cr=y rb=1 relic=n 7j=y` starts immediately, no board. Omitted flags reuse the last `/start`.

| Flag | Values | Meaning |
| --- | --- | --- |
| `fs` | `y`/`n` | Fast Start |
| `cr` | `y`/`n` | Cookie Relay |
| `rb` | `1`–`11`/`n` | Random Boost number, `n` = off |
| `relic` | `y`/`n` | Open + claim relics |
| `7j` | `y`/`n` | 7 jumps at run start |

Boost numbers: 1 Double Coins · 2 +15% Score · 3 −15% HP Drain · 4 Revive 80 HP · 5 70% Crush · 6 +17% Speed · 7 Gold Coin Magic · 8 −30% Collision · 9 +20% Potion HP · 10 Magnetic Aura · 11 2 Pit Lifts

Other commands: `/pause` (bot stops, game stays open), `/kill` (force stop), `/status`, `/logs [n]` (max 100), `/screenshot`, `/guide`, `/help`.

</details>

<details><summary>How the controller runs (tmux + caffeinate)</summary>

`cookiebot.sh` wraps: `tmux new -d -s cookiebot "source .venv/bin/activate && caffeinate -i python3 controller.py"`. `caffeinate -i` stops macOS idle sleep from killing the Telegram long-poll (the `Connection reset by peer` errors). `cookiebot.sh` commands: `start` `stop` `restart` `status` `logs` `attach` (detach: `Ctrl+b` then `d`).

Notes: the controller launches/closes the *game*, not BlueStacks. `/start` waits ~60s for the game to boot before giving up. `/stop` and `/kill` wait 10s/2s for the bot to exit, then force-close the game. `/pause` waits 10s; `/status` shows PAUSING meanwhile.

</details>

## Options the bot asks about

| Option | What it does |
| --- | --- |
| Fast Start | Buys + uses Fast Start every run |
| Cookie Relay | Buys + uses Cookie Relay every run |
| Random Boost | Buys your chosen boost (of 11) every run |
| Detect Relic | Opens + claims relic chests |
| 7 jumps | Taps jump 7× (0.6–0.9s apart) at each run start |

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Stage not detected | Template mismatch | Save a debug screen (`debug.py`), recapture the template at 1280×720 |
| Wrong taps | Not 1280×720 | Set emulator resolution to 1280×720 |
| ADB failed | Device not reachable | `adb devices`; `adb connect 127.0.0.1:5555` |
| `/start` answers wrong prompts | Prompt order changed in `bot.py` | Update `flags_to_answers()` in `controller.py` to match (see coupling note) |
| Controller won't start | Empty allowlist | Set `TELEGRAM_ALLOWED_USER_IDS` in `.env` |

> **Coupling note:** `/start` flags map to the *order* of prompts in `prompt_user_options()` (`bot.py`): Fast Start, Cookie Relay, Random Boost (+ number), Relic, then 7 jumps. Change the prompts → update `flags_to_answers()` in `controller.py`. Verify with `/logs`: look for `✅ Selected: <boost>`.

## Config (`config.py`)

| Constant | Default | Meaning |
| --- | --- | --- |
| `DEVICE_IP` / `DEVICE_PORT` | `127.0.0.1` / `5555` | Your emulator's ADB address |
| `MATCH_THRESHOLD` | `0.8` | Template match confidence (0–1) |
| `SESSION_RESET_INTERVAL` | `(5400, 10800)` | App restart every 1.5–3 h |
| `LIVES_INTERVAL` | `(10800, 21600)` | Mailbox lives check every 3–6 h |
| `MAX_UNKNOWN_STAGE_RESTARTS` | `3` | Restarts before the bot gives up and alerts you |

Telegram keys (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_USER_IDS`) load from `.env`. Each stage's detection region has a `STAGE_<NAME>_REGION` constant you can tune.

<details><summary>How it works, project layout, template list, debugging</summary>

**Loop:** screenshot every 0.25s → `matchTemplate` against stage images (grouped PRE_GAME / IN_GAME / POST_GAME) → tap the matching button with human-like jitter. Full-scan fallback if grouped detection keeps missing.

```
main.py        # entry
bot.py         # game loop + option prompts
actions.py     # tap handlers per stage
controller.py  # Telegram remote control
detection.py   # template matching
adb.py         # ADB + screen capture
config.py      # all constants
alerts.py      # Telegram crash alerts
templates/     # detection images (1280×720)
tests/         # unit tests (python3 -m pytest tests/)
```

Templates are committed. If a game update changes a screen, recapture that template at 1280×720.

**Debug a screen:** `save_debug_screen(device_capture_screen(DEVICE_IP, DEVICE_PORT))` from `debug.py` → saved to `debug_screens/`.

</details>
