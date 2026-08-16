# Focus Time Tracker

<div align="center">

[中文](README.md) | **English**

</div>

A Windows desktop tool built with Python that automatically detects the currently focused foreground window/process, records usage duration, and visualizes it with a light-themed interface.

The UI features a blue accent color scheme, rounded cards and controls, a left-side navigation bar, and an overall lightweight, flat design.

## Features

- **Foreground Window Capture**: Samples the current foreground window via Win32 API (using only the standard library `ctypes`), identifies desktop apps, games, browsers, and desktop/lock screen, and automatically classifies them
- **Game Identification Rule Engine**: Instead of maintaining individual game names, it uses "launcher / install directory" keywords (Steam / Riot / Epic / Battle.net / WeGame / miHoYo, etc.) + process name rules for automatic identification. Unreal Engine clients (`-Win64-Shipping.exe`) are automatically covered. Rules can be added or removed in `game_rules.json`
- **Specific Website Identification**: Matches browser window titles against browser history databases (SQLite) to determine the current website. Supports Chrome / Edge / Firefox; all matching is done locally with no network calls or data uploads
- **Automatic Website Deduplication**: Suffixes like "and N other pages" in multi-tab window titles are cleaned up, and the same website is automatically merged for statistics
- **Session Recording**: Accumulates duration per "process / website", stored in SQLite (`data/usage.db`, WAL mode, supports concurrent GUI reads + background writes)
- **Light-themed GUI**: Five pages — Home (Overview), Statistics, Detailed Records, Categories, and Settings — with a left navigation bar + right content area
- **Statistics Charts**: Today's hourly usage bar chart, 7-day category trend chart (rounded bars, average line), auto-scales with window resizing
- **Detailed Records Merged by Hour**: Same process/website within the same hour is merged into one row, displaying the time range `XX:00–(XX+1):00` with total duration and occurrence count
- **Full Category Display**: Three cards for Apps / Games / Websites, showing all processes/websites in each category with duration and percentage of category total. Names are fully displayed (auto-wrapping)
- **Manual Category Override**: Each process row in the Categories page has a "Move to Game / Move to App" button. Clicking permanently assigns the process to the target category, written to `category_overrides.json` and effective immediately. "Restore Auto" clears the override and reverts to rule-engine classification
- **System Tray Persistence**: Minimizes to tray while continuing to record; tray menu can restore the main window or exit
- **Mutual-Exclusion Capture**: `dashboard` and `start` share the same lock, ensuring only one capture process writes to the database at a time to prevent duplicate duration counting
- **Real-time Refresh**: Home, Categories, and Settings pages refresh data every 2 seconds

## Screenshots

![Home](docs/首页.png)

![Statistics](docs/统计.png)

![Detailed Records](docs/详细记录.png)

![Categories](docs/分类.png)

![Settings](docs/设置.png)

## Requirements

- Windows 10 / 11 (depends on Win32 API; macOS / Linux not supported)
- Python 3.10+ (code uses `str | None` and other modern syntax)
- Dependencies:
  - `matplotlib>=3.5` (charts; see `requirements.txt`)
  - Pillow (icon and logo display; falls back to a default placeholder if missing)

## Quick Start

```bash
cd focus-time-tracker
python -m pip install -r requirements.txt
python -m pip install pillow        # Recommended, used for icons / logo

python main.py dashboard            # Open the GUI (automatically starts background capture)
python main.py start                # Background capture only, no GUI (Ctrl+C to stop)
python main.py stats                # Print today's stats to console
python main.py report               # Generate today's report image
python main.py report --days 7      # Generate 7-day trend chart
```

> Note: `dashboard` automatically launches a separate background capture process. `start` also launches capture. The two are mutually exclusive — **do not run both at the same time**, or durations will be double-counted.

Want to see it in action? Run `python main.py demo` to generate 7 days of sample data (this will be mixed with existing data; back up `data/usage.db` first if needed), then open the GUI or run `python main.py report --days 7`.

## Command Reference

| Command | Description |
| --- | --- |
| `python main.py dashboard` | Open the GUI (automatically starts background capture) |
| `python main.py start` | Background capture (foreground loop, Ctrl+C to stop) |
| `python main.py stats` | Print today's statistics to console |
| `python main.py report` | Generate today's report PNG |
| `python main.py report --days N` | Generate N-day trend chart |
| `python main.py now` | View current foreground window info (diagnostic) |
| `python main.py game rules` | Print currently active game identification rules |
| `python main.py game check --path "C:\Riot Games\VALORANT\live\VALORANT.exe" --process VALORANT.exe` | Check if a process/path is classified as a game by current rules |
| `python main.py unlock` | Clear stale capture lock (use when prompted "capture session already running") |
| `python main.py demo` | Generate 7 days of sample data for preview |

## GUI Pages

- **Home (Overview)**: "Today / This Week" toggle in the top-right; "Most Frequent" section with Apps / Websites cards (side-by-side on wide screens, auto-stacked on narrow windows), each row = icon + name + progress bar + duration; category dropdown and 4 stat cards below
- **Statistics**: Today's hourly usage bar chart, 7-day category trend stacked bar chart; rounded bars, average line (only counting days/hours with usage), auto-scales with window
- **Detailed Records**: Grouped by "hour + same process/website", time range `XX:00–(XX+1):00`, duration and count (×N)
- **Categories**: Three cards for Apps / Games / Websites (three columns on wide screens, stacked on narrow windows), showing all processes/websites in each category with duration and percentage of category total, names fully displayed; Apps / Games rows have "Move to Game / Move to App" buttons (shows "Restore Auto" if manually overridden), clicking immediately reclassifies the process and its history
- **Settings**: Sampling interval, minimum session duration, website identification toggle (three rows vertically), excluded processes (capsule tags, click to remove individually), Save / Stop Tracking / Quit, data directory, current foreground window preview

## Configuration (config.json)

```json
{
  "poll_interval_seconds": 1.0,
  "min_session_seconds": 3,
  "exclude_processes": ["python.exe"],
  "browser_site_tracking": true,
  "data_dir": "data",
  "report_dir": "reports"
}
```

- `poll_interval_seconds`: Sampling interval in seconds; smaller = more precise, larger = less resource usage
- `min_session_seconds`: Window switches shorter than this duration are not recorded, preventing fragmented data
- `exclude_processes`: Process names to exclude from tracking, e.g. `["explorer.exe"]`; no app duration is accumulated while these processes are in the foreground
- `browser_site_tracking`: Whether to identify specific websites visited in browsers; when disabled, browsers are tracked as regular applications
- `data_dir` / `report_dir`: Data and report directories (relative to project root, or absolute paths)

## Game Identification Rules (game_rules.json)

Game classification is determined by a "rule engine" rather than individually maintaining game names. New platforms/games can be added without changing code:

- **path_keywords**: Install directory keywords; a match classifies as a game. Defaults include `steamapps` (Steam), `riot games` (VALORANT / League of Legends), `epic games`, `battle.net` / `battlenet` / `blizzard`, `ubisoft`, `ea games`, `gog galaxy`, `wegame` / `rail_apps` (Tencent WeGame), `hoyoplay` / `mihoyo` / `hoyoverse` (miHoYo), `rockstar games`, `2k games`, `netease`, etc.
- **process_suffix**: Process name suffix characteristics. Defaults include `-win64-shipping.exe` / `-win32-shipping.exe`, the standard naming convention for Unreal Engine release clients, automatically covering Delta Force and a large number of UE games
- **process_exact**: Exact process name matches (lowercase, with `.exe`), used as a fallback, e.g. `valorant.exe`, `genshinimpact.exe`
- **exclude_path_keywords** / **exclude_process_exact**: Exclusion rules to prevent directory keyword false positives on regular software (defaults already exclude NetEase Cloud Music, Youdao, WeChat, QQ)

Rules **append to the built-in list** by default with automatic deduplication (e.g., to add a custom game directory, just add a line in `path_keywords`). To completely replace the built-in list, set `replace_defaults` to `true`. Changes to `game_rules.json` take effect after restarting the capture process (historical data is automatically backfilled with the new rules the next time the database is opened). To check how a process would be classified:

```bash
python main.py game check --process VALORANT.exe --path "C:\Riot Games\VALORANT\live\VALORANT.exe"
python main.py game rules
```

> Note: When running from source, `game_rules.json` is read from the project root directory. The packaged exe reads `game_rules.json` from the same directory as the exe.

## Manual Category Overrides (category_overrides.json)

Automatic rules occasionally misclassify (e.g., a game not installed in a rule-covered directory, or regular software caught by a directory keyword). You can adjust manually in the **Categories** page without changing code:

- Each process row in the Apps / Games cards has a "Move to Game / Move to App" button on the right. Clicking permanently assigns the process to the target category, and historical records are reclassified accordingly
- Manually overridden processes show a "Restore Auto" button next to the override button. Clicking clears the manual setting and reverts to rule-engine classification (historical records are re-evaluated by the rules)
- Overrides are stored in `category_overrides.json` in the same directory as the exe (or project root when running from source), in the format: `{ "process_name_lowercase": "app" | "game" }`, e.g. `{"cs2.exe": "app"}` to manually classify CS2 as an app
- Classification priority: **Manual override > Game rules > Website > App**; changes take effect **immediately** without restarting capture

## Auto-Start on Boot

Method 1: Place a shortcut to `启动屏幕时间.vbs` in the Startup folder (`Win + R` → `shell:startup`). Double-clicking launches with no console window, automatically starting background capture and opening the GUI.

Method 2: Place a shortcut in the Startup folder with the target set to:

```text
C:\...\pythonw.exe C:\...\focus-time-tracker\main.py start
```

## Packaging as exe (No Python Installation Required)

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name "Focus Time Tracker" --icon assets\logo.ico `
  --add-data "assets;assets" launcher.py
```

- The output is in `dist\Focus Time Tracker\`. Copy the **entire folder** (onedir mode — you cannot copy just the exe)
- After packaging, copy `config.json` and `game_rules.json` to the same directory as the exe for custom configuration and game rules to take effect (`category_overrides.json` is written by the Categories page and auto-generated in the exe directory)
- The data directory is `data\` next to the exe (moves with the folder); it is automatically created on first run
- Double-clicking the exe opens the GUI and starts background capture automatically; clicking the × in the top-right minimizes to the system tray while continuing to record
- To build as a single exe file: change `--onedir` to `--onefile` (slower startup, larger file size)

## Data & Privacy

- Database: `data/usage.db` (SQLite), report images: `reports/` directory
- `data/` and `reports/` are runtime-generated data, excluded by `.gitignore` and **not** committed to the repository
- Website identification and duration tracking are all performed locally — no network calls, no data uploads
- To clear records and start fresh: exit the program, then delete `data/usage.db` (along with `-wal` / `-shm` files)

## FAQ

- **How are websites identified?** Browser window titles typically match the current page title. The program matches the title against recent entries in the browser history database (SQLite) to determine the site domain and full URL. In private mode or when history is cleared, it falls back to displaying the window title.
- **Why don't websites duplicate in Categories?** Noise in multi-tab window titles ("X and N other pages - Personal - Microsoft Edge") is cleaned up, and the same website is automatically merged. Dirty historical data is also merged by cleaned site name when the GUI opens.
- **Statistics chart only shows the top-left corner after switching pages?** Fixed: chart dimensions are always calibrated to the actual canvas size; switching pages or resizing the window no longer causes misalignment.
- **Identification seems inaccurate?** Use the "Move to Game / Move to App" button on the corresponding process row in the Categories page for manual adjustment — it takes permanent effect. Click "Restore Auto" to revert to rule-engine identification. False-positive rules themselves can be modified in `game_rules.json`.
- **`python main.py now` shows "No foreground window captured"**: This means it is running in a non-interactive desktop/session (remote session, scheduled task, service, etc.). Please run it from your normal logged-in desktop terminal.
- **Cross-midnight sessions**: Long sessions spanning midnight are automatically split by date, assigned to their respective days.
- **Home / Categories page incomplete after midnight?** Fixed (v1.2): After midnight, the page automatically switches to today's data and force-refreshes all cards. If a category has no records today, it shows "No records today · X yesterday" — data is not lost. You can switch to "This Week" on the Home page or check Detailed Records.

## Project Structure

```text
focus-time-tracker/
├── main.py                 # CLI entry point
├── config.json             # Configuration
├── game_rules.json         # Game identification rules (customizable)
├── category_overrides.json # Manual category overrides (written by Categories page, optional)
├── requirements.txt
├── 启动屏幕时间.vbs         # Console-less launcher (background capture + GUI)
├── assets/                 # Brand logo (logo.png / logo.ico)
├── tracker/
│   ├── monitor.py          # Win32 foreground window capture + recording main loop
│   ├── games.py            # Game identification rule engine
│   ├── db.py               # SQLite storage and aggregation queries
│   ├── browser.py          # Browser website identification (Chrome/Edge/Firefox)
│   ├── report.py           # CLI report charts
│   ├── app.py              # GUI (Home/Statistics/Detailed Records/Categories/Settings)
│   ├── widgets.py          # Rounded controls, process icons and app name parsing
│   ├── theme.py            # Light theme (colors/fonts/rounded corners)
│   ├── tray.py             # System tray
│   ├── demo.py             # Demo data generation
│   └── utils.py            # Utility functions (duration formatting, site name cleaning)
├── data/                   # Runtime-generated (SQLite, excluded by .gitignore)
└── reports/                # Runtime-generated (PNG reports, excluded by .gitignore)
```
