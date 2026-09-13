# CookieRun Classic Bot

An automated bot for CookieRun Classic that uses ADB (Android Debug Bridge) and OpenCV template matching to detect game stages and automate gameplay on an Android device or emulator.

## Features

- Automatically detects game stages via screen capture and template matching
- Handles the full game loop: Main Menu → Purchase Items → Game Start → Relay → Game Complete → Mystery Box → Congratulations → Level Up → Relic Complete → Relic Claim → Daily Check-in → Daily Treasure → Enter League → League Results → Previous Rank Results → Anti-Bot → Connection Lost → Inactive
- Optional **Fast Start** power-up (auto-purchase and use)
- Optional **Cookie Relay** power-up (auto-purchase and use)
- Optional **Desired Random Boost** (auto-purchase the selected boost from 11 available options)
- Human-like tap behavior with randomized coordinate jitter and delays
- Auto-waits 30–60 seconds between games to reduce detection risk
- Periodic session reset: automatically restarts the app every 1.5–3 hours to avoid long-session detection
- Anti-bot card challenge detection: identifies and taps the odd card out automatically
- Debug screen saving for troubleshooting
- Telegram alerts on crashes and unrecoverable stuck states
- Optional Telegram remote control (`controller.py`) — start/stop the bot, check status, tail logs, and pull a live screenshot from your phone

## Quickstart (macOS + BlueStacks)

Tested and confirmed working end-to-end on macOS with BlueStacks Air (Apple Silicon). Takes ~20 minutes.

1. **Install ADB:**
   ```bash
   brew install android-platform-tools
   ```

2. **Install BlueStacks Air** from [bluestacks.com](https://www.bluestacks.com) (Apple Silicon build), launch it.

3. **Set the emulator resolution to 1280×720** — BlueStacks Settings → Display.

4. **Enable ADB debugging in BlueStacks** — Settings → Advanced → toggle Android Debug Bridge on.

5. **Install CookieRun Classic** inside BlueStacks (Play Store or sideload the APK).

6. **Connect ADB to the emulator:**
   ```bash
   adb connect 127.0.0.1:5555
   adb devices    # should show 127.0.0.1:5555   device
   ```
   If BlueStacks uses a different port, use that instead — check with `adb devices`.

7. **Set up the Python environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

8. **Confirm `config.py`** has `DEVICE_PORT` matching the port from step 6 (`5555` for BlueStacks; MuMu Player defaults to `7555`).

9. **Run it:**
   ```bash
   source .venv/bin/activate
   python3 main.py
   ```

Leave BlueStacks running with CookieRun Classic open before starting the bot. Stop the bot anytime with `Ctrl+C`.

## Remote Control via Telegram (optional)

`controller.py` lets you run the bot's terminal remotely from Telegram — useful if the bot runs on a MacBook you're not sitting in front of (e.g. controlling it from your phone).

It does **not** replace `python3 main.py`; instead it spawns/stops that same process for you and answers its prompts over stdin, so `bot.py` itself is unchanged.

### Setup

1. **Create a Telegram bot** via [@BotFather](https://t.me/BotFather) and copy the token it gives you.
2. **Find your numeric Telegram user id** — message [@userinfobot](https://t.me/userinfobot) and it'll reply with your id.
3. **Add both to `.env`** (copy `.env.example` if you haven't already):
   ```
   TELEGRAM_BOT_TOKEN=123456:abc-your-bot-token
   TELEGRAM_ALLOWED_USER_IDS=111111111
   ```
   `TELEGRAM_ALLOWED_USER_IDS` is comma-separated if you want more than one person to be able to control it. **Leave it empty and the controller refuses to start** — this prevents a leaked token from letting a stranger spawn processes on your Mac.
4. **Message your bot once** on Telegram (anything, e.g. `/help`) so it can find your chat.
5. **Install tmux** (keeps the controller running after you close the terminal) and confirm `caffeinate` is available (it ships with macOS):
   ```bash
   brew install tmux
   which caffeinate   # should print /usr/bin/caffeinate
   ```

### Running the controller

Run it inside `tmux`, wrapped in `caffeinate -i` so macOS doesn't idle-sleep and drop the connection to Telegram:

```bash
cd /path/to/cookierun-classic-bot
tmux new -d -s cookiebot "source .venv/bin/activate && caffeinate -i python3 controller.py"
```

> **Why `caffeinate -i`?** macOS's idle sleep (default ~15 min) tears down the network connection `controller.py` holds open for Telegram long-polling, which shows up as repeated `Connection reset by peer` / `read operation timed out` errors. `caffeinate -i` blocks idle sleep for as long as this process is running, and releases automatically when it exits.

Managing the session:

| Task | Command |
| --- | --- |
| Start it (only if not already running) | `tmux new -d -s cookiebot "source .venv/bin/activate && caffeinate -i python3 controller.py"` |
| Check it's running | `tmux ls` — should list `cookiebot` |
| View live output | `tmux attach -t cookiebot` |
| Detach without stopping it | Press `Ctrl+b` then `d` (do **not** press `Ctrl+d` or type `exit` — that kills the session) |
| Stop it | `tmux kill-session -t cookiebot` |
| Restart after a crash/reboot | Run the "Start it" command again — `tmux new -s cookiebot` fails with "session already exists" if it's already up, so check `tmux ls` first |

### Commands

| Command | Effect |
| --- | --- |
| `/start [fs=y\|n cr=y\|n rb=1-11\|n relic=y\|n]` | Launches CookieRun Classic via ADB if it isn't already running, waits for it to come up, then starts the bot, piping the flags to `main.py`'s interactive prompts. Any flag you omit reuses the value from the last `/start`. Bare `/start` reruns with the same flags as last time. |
| `/stop` | Sends `SIGINT` to the bot process — identical to pressing `Ctrl+C` at the terminal. Graceful; the bot finishes its current step, exits cleanly, then the controller force-stops CookieRun Classic. |
| `/kill` | Sends `SIGKILL`, then force-stops CookieRun Classic. Use only if `/stop` doesn't take effect. |
| `/status` | Running/stopped, uptime, and the last line of `logs/bot.log`. |
| `/logs [n]` | Last `n` lines of `logs/bot.log` (default 30, max 100). |
| `/screenshot` | Captures the current BlueStacks screen via ADB and sends it as a photo. Works even if the bot is stopped or stuck, since it captures independently of the bot process. |
| `/help` | Lists all commands. |

Example:
```
/start fs=y cr=y rb=1 relic=n
```
This is equivalent to running `python3 main.py` and answering `y`, `y`, `y`, `1`, `n` to its prompts (Fast Start, Cookie Relay, Desired Random Boost, boost #1 = Double Coins, Detect Relic). `rb` is the number from the `Select desired boost` list `bot.py` prints (1–11); use `rb=n` to skip that power-up entirely.

The controller replies with the exact answer sequence it sent, and you can double-check it landed on the right prompts by running `/logs` and looking for the `✅ Selected: <boost name>` line.

> **⚠️ Coupling note:** the `/start` flags map to the *order* of the questions in `prompt_user_options()` (`bot.py`). If those prompts are ever reordered or a new one is added, `controller.py`'s flag mapping needs to be updated to match, or answers will land on the wrong question. Always sanity-check a fresh `/start` against `/logs` after changing `bot.py`'s prompts.

> **App lifecycle notes:**
> - BlueStacks Air itself must already be running (the controller launches/closes the *game*, not the emulator).
> - `/start` waits up to ~60s total for CookieRun Classic to launch (15s initial wait + up to 45s polling `pidof`) before giving up and replying with an error — it does **not** start the bot if the app fails to launch.
> - `/stop`/`/kill` wait briefly (10s / 2s respectively) for the bot process to actually exit before force-stopping the app, so it isn't still mid-tap when the app closes. The app is force-stopped either way, even if the process didn't exit in time.
> - This is independent of `bot.py`'s own `device_reset_app()` calls (used internally to recover from crashes/unknown stages while the bot is running) — those are unaffected by this change.

### Typical session from your phone

1. Make sure BlueStacks Air itself is running (the controller launches CookieRun Classic for you, but not the BlueStacks emulator).
2. On your Mac, confirm the controller is up: `tmux ls` should list `cookiebot`. If not, see [Running the controller](#running-the-controller) above.
3. In Telegram, open your bot's chat and send:
   ```
   /start fs=y cr=y rb=1 relic=n
   ```
   The controller launches CookieRun Classic (if it isn't already open), waits for it to come up, then starts the bot and replies with the pid and the exact stdin sequence sent.
4. Send `/logs` — confirm you see `✅ Selected: Double Coins` (or whichever boost you picked) and that the bot is progressing through stages.
5. Send `/screenshot` anytime to see what BlueStacks is currently showing.
6. Send `/status` to check uptime and the last thing the bot did.
7. When you're done, send `/stop` (graceful) — the bot exits, then CookieRun Classic is closed automatically; you'll get a confirmation for each. If it doesn't stop within a minute or two, send `/kill`.

## Requirements

- Python 3.x
- [ADB (Android Debug Bridge)](https://developer.android.com/tools/adb) installed and in your system PATH
- An Android device or emulator running CookieRun Classic
- **Device screen resolution must be set to 1280×720**

### Python Dependencies

```
numpy
opencv-python
```

Install via:

```bash
pip install -r requirements.txt
```

## Setup

1. **Enable ADB on your device/emulator** and note its IP address and port.

2. **Edit `config.py`** and set your device details at the top:

   ```python
   DEVICE_IP = "127.0.0.1"    # Your device's IP address
   DEVICE_PORT = 5555         # Your device's ADB port
   ```

3. **Ensure templates exist** — the `templates/` folder must contain the following detection images captured at 1280×720:

   **Stage templates:**
   `MAINMENU_1.png`, `PURCHASE_ITEM_1.png`, `GAME_START_1.png`, `GAME_RELAY_1.png`,
   `GAME_COMPLETE_1.png`, `MYSTERY_BOX_1.png`, `CONGRATULATIONS_1.png`, `CONGRATULATIONS_2.png`,
   `LEVEL_UP_1.png`, `RELIC_COMPLETE_1.png`, `RELIC_CLAIM_1.png`, `DAILY_CHECKIN_1.png`,
   `DAILY_TREASURE_1.png`, `ENTER_LEAGUE_1.png`, `LEAGUE_RESULTS_1.png`,
   `PREVIOUS_RANK_RESULTS_1.png`, `ANTI_BOT_1.png`, `CONNECTION_LOST_1.png`, `INACTIVE_1.png`

   **Boost templates** (only needed if using Desired Random Boost):
   `BOOST_DOUBLE_COINS_1.png`, `BOOST_15P_SCORE_BONUS_1.png`, `BOOST_M15P_HP_DRAIN_1.png`,
   `BOOST_REVIVE_ONCE_WITH_80HP_1.png`, `BOOST_70P_CRUSH_CHANCE_1.png`, `BOOST_17P_BASE_SPEED_1.png`,
   `BOOST_GOLD_COIN_MAGIC_1.png`, `BOOST_M30P_COLLISION_DAMAGE_1.png`, `BOOST_20P_HP_FROM_POTIONS_1.png`,
   `BOOST_MAGNETIC_AURA_1.png`, `BOOST_2PIT_LIFTS_1.png`

## Usage

```bash
python main.py
```

On startup, the bot will:

1. Connect to your ADB device
2. Prompt you to configure options:
   - **Fast Start**: auto-buy and use the Fast Start item each round
   - **Cookie Relay**: auto-buy and use the Cookie Relay item each round
   - **Desired Random Boost**: auto-buy a specific boost each round (choose from 11 options)
3. Begin the automation loop

## Project Structure

```
cookierun-classic-bot/
├── main.py              # Entry point
├── bot.py               # Main bot logic and game loop
├── actions.py           # Tap/action handlers for each stage
├── adb.py               # ADB connection and screen capture
├── alerts.py            # Telegram alert notifications
├── config.py            # Device config, thresholds, and template paths
├── controller.py        # Optional Telegram remote control (start/stop/status/logs/screenshot)
├── detection.py         # OpenCV template matching logic
├── debug.py             # Debug screen saving utility
├── env.py               # .env file loader
├── requirements.txt     # Python dependencies
├── templates/           # Stage detection template images
├── tests/               # Unit tests
├── debug_screens/       # Saved debug screenshots (auto-created)
└── logs/                # Bot log + controller state (auto-created, gitignored)
```

## Configuration

All constants are defined in `config.py`.

| Constant                 | Default         | Description                                             |
| ------------------------ | --------------- | ------------------------------------------------------- |
| `DEVICE_IP`              | `127.0.0.1`     | ADB device IP address                                   |
| `DEVICE_PORT`            | `5555`          | ADB device port                                         |
| `MATCH_THRESHOLD`        | `0.8`           | Minimum confidence for template match (0.0–1.0)         |
| `TEMPLATE_DIR`           | `templates`     | Folder containing stage detection images                |
| `SESSION_RESET_INTERVAL` | `(5400, 10800)` | Random interval in seconds (1.5–3 h) before app restart |
| `LIVES_INTERVAL`         | `(1500, 2100)`  | Random interval in seconds (25–35 min) before sending/receiving friend lives |
| `MAX_UNKNOWN_STAGE_RESTARTS` | `3`         | Consecutive stage-unknown app restarts before the bot gives up and stops |
| `TELEGRAM_BOT_TOKEN`     | (empty)         | Bot token from [@BotFather](https://t.me/BotFather), used for both alerts and remote control |
| `TELEGRAM_CHAT_ID`       | (empty)         | Chat id that crash/stuck alerts (`alerts.py`) are sent to |
| `TELEGRAM_ALLOWED_USER_IDS` | (empty)      | Comma-separated Telegram user ids allowed to use `controller.py` — see [Remote Control via Telegram](#remote-control-via-telegram-optional) |

You can also limit specific stage detection to a fixed screen region with `(x1, y1, x2, y2)` bounds. Each stage has a corresponding `STAGE_<NAME>_REGION` constant in `config.py` that restricts template matching to a sub-region of the screen, which speeds up detection.

## How It Works

1. The bot captures a screenshot via ADB approximately every 0.25 seconds.
2. It uses OpenCV's `matchTemplate` to compare the screen against known stage images within pre-defined screen regions.
3. Stage detection is split into `PRE_GAME`, `IN_GAME`, and `POST_GAME` groups so each loop checks only the stages relevant to the current flow. `CONNECTION_LOST` and `INACTIVE` are always checked regardless of group.
4. If grouped detection misses repeatedly (configurable timeout per group), the bot automatically falls back to a full stage scan to recover.
5. When a stage is detected, the corresponding action is performed (tapping buttons with randomized coordinate jitter and delays).
6. **Anti-bot challenge**: when the anti-bot card screen is detected, the bot identifies the odd card out from a set of six using template matching and taps it.
7. **Session reset**: after a random interval of 1.5–3 hours, the bot restarts the CookieRun app to reduce long-session detection risk.
8. The loop continues until the process is manually stopped (`Ctrl+C`).

## Debugging

To capture and save a screenshot for troubleshooting template matching, call `save_debug_screen()` from `debug.py`:

```python
from adb import device_capture_screen
from config import DEVICE_IP, DEVICE_PORT
from debug import save_debug_screen

device_screen = device_capture_screen(DEVICE_IP, DEVICE_PORT)
save_debug_screen(device_screen)
```

Saved screenshots will appear in the `debug_screens/` folder with a timestamp.

## Troubleshooting

- **Stage not detected**: Save a debug screenshot and compare it against the template images. You may need to recapture templates at the correct resolution.
- **ADB connection failed**: Verify ADB is in your PATH and the device is reachable (`adb devices`).
- **Wrong tap coordinates**: All coordinates are calibrated for **1280×720**. Ensure your emulator/device is set to this resolution.
- **`/start` answers land on the wrong prompt**: `controller.py`'s flags assume the exact question order in `prompt_user_options()` (`bot.py`). Check `/logs` for the `✅ Selected: <boost>` confirmation after starting; if it's missing or wrong, the prompts may have changed since the mapping was written.
- **Controller refuses to start**: `TELEGRAM_ALLOWED_USER_IDS` must be non-empty in `.env`. This is intentional — it prevents a leaked bot token from giving a stranger control of your Mac.
