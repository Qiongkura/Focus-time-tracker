# Focus-time-tracker

<div align="center">

[中文](README.md) | **English**

</div>

A Windows desktop tool built with Python that automatically detects the currently focused foreground window/process, records usage duration, and visualizes it with a light-themed interface.

- **Foreground Window Capture**: Samples the current foreground window via Win32 API (using only the standard library `ctypes`), identifies desktop apps, games, browsers, and desktop/lock screen, and automatically classifies them;
- **Game Identification Rule Engine**: Uses "launcher / install directory" keywords + process name rules for automatic identification. Supports Steam / Riot / Epic / Battle.net / WeGame / miHoYo and more. Rules can be customized in `game_rules.json`;
- **Specific Website Identification**: Matches browser window titles against browser history databases (SQLite) to determine the current website. Supports Chrome / Edge / Firefox; all matching is done locally with no network calls or data uploads.

## Features

| Feature | Description |
| --- | --- |
| Foreground Window Capture | Samples the current foreground window via Win32 API, automatically identifies and classifies desktop apps, games, browsers, desktop/lock screen |
| Game Identification Rule Engine | Uses "launcher / install directory" keywords + process name rules for automatic identification, no need to maintain individual game names |
| Specific Website Identification | Matches browser window titles against browser history databases to determine the current website, supports Chrome / Edge / Firefox |
| Automatic Website Deduplication | Suffixes like "and N other pages" in multi-tab window titles are cleaned up, same website is automatically merged for statistics |
| Session Recording | Accumulates duration per "process / website", stored in SQLite (WAL mode, supports concurrent GUI reads + background writes) |
| Light-themed GUI | Five pages — Home (Overview), Statistics, Detailed Records, Categories, and Settings — with left navigation bar + right content area |
| Statistics Charts | Today's hourly usage bar chart, 7-day category trend chart (rounded bars, average line), auto-scales with window resizing |
| Detailed Records Merged by Hour | Same process/website within the same hour is merged into one row, displaying time range with total duration and occurrence count |
| Full Category Display | Three cards for Apps / Games / Websites, showing all processes/websites in each category with duration and percentage of category total |
| Manual Category Override | Each process row has a "Move to Game / Move to App" button. Clicking permanently assigns to the target category, effective immediately |
| System Tray Persistence | Minimizes to tray while continuing to record; tray menu can restore the main window or exit |
| Mutual-Exclusion Capture | `dashboard` and `start` share the same lock, ensuring only one capture process writes to the database at a time |
| Real-time Refresh | Home, Categories, and Settings pages refresh data every 2 seconds |

## Architecture

- **Capture Layer** (`tracker/monitor.py`): Win32 API polls the foreground window every 2 seconds, identifies window process, title, and path, calls the classification engine, writes to SQLite
- **Classification Engine** (`tracker/games.py`): Priority is "Manual override > Game rules > Website > App". Rules support path_keywords / process_suffix / process_exact / exclusion rules
- **Browser Identification** (`tracker/browser.py`): Matches Chrome / Edge / Firefox history databases (SQLite) to extract the current website domain
- **Storage Layer** (`tracker/db.py`): SQLite WAL mode, supports concurrent GUI reads and background writes; automatically splits cross-midnight sessions by day
- **GUI Layer** (`tracker/app.py`): Five pages (Home/Statistics/Detailed Records/Categories/Settings), light theme, rounded controls, left navigation bar
- **System Tray** (`tracker/tray.py`): Minimizes to tray while continuing to record, supports restoring main window and exiting

## Requirements

```bash
Windows 10 / 11 (depends on Win32 API; macOS / Linux not supported)
Python 3.10+ (uses str | None and other modern syntax)
```

Dependencies:
- `matplotlib>=3.5` (charts)
- `Pillow` (icon and logo display; falls back to a default placeholder if missing)

## Install & Usage

```bash
cd focus-time-tracker
python -m pip install -r requirements.txt
python -m pip install pillow        # Recommended, used for icons / logo
```

> **Note**: `dashboard` automatically launches a separate background capture process. `start` also launches capture. The two are mutually exclusive — **do not run both at the same time**, or durations will be double-counted.

Command reference:

| Command | Description |
| --- | --- |
| `python main.py dashboard` | Open the GUI (automatically starts background capture) |
| `python main.py start` | Background capture (foreground loop, Ctrl+C to stop) |
| `python main.py stats` | Print today's statistics to console |
| `python main.py report` | Generate today's report PNG |
| `python main.py report --days N` | Generate N-day trend chart |
| `python main.py now` | View current foreground window info (diagnostic) |
| `python main.py game rules` | Print currently active game identification rules |
| `python main.py game check --path "..." --process ...` | Check if a process/path is classified as a game |
| `python main.py unlock` | Clear stale capture lock |
| `python main.py demo` | Generate 7 days of sample data for preview |

## Usage Example

```bash
# Start GUI with automatic capture
python main.py dashboard

# Background capture only, no GUI
python main.py start

# View today's statistics
python main.py stats

# Generate 7-day trend chart
python main.py report --days 7

# Diagnostic: view current foreground window
python main.py now

# Test game identification rules
python main.py game check --process VALORANT.exe --path "C:\Riot Games\VALORANT\live\VALORANT.exe"

# Generate sample data for preview
python main.py demo
```

## Configuration

### config.json

| Key | Description | Default |
| --- | --- | --- |
| `poll_interval_seconds` | Sampling interval in seconds; smaller = more precise, larger = less resource usage | `1.0` |
| `min_session_seconds` | Window switches shorter than this duration are not recorded, preventing fragmented data | `3` |
| `exclude_processes` | Process names to exclude from tracking, e.g. `["explorer.exe"]` | `["python.exe"]` |
| `browser_site_tracking` | Whether to identify specific websites visited in browsers; disabled = browsers tracked as regular apps | `true` |
| `data_dir` | Data directory (relative to project root, or absolute path) | `"data"` |
| `report_dir` | Report directory (relative to project root, or absolute path) | `"reports"` |

### game_rules.json

Game classification is determined by a "rule engine" rather than individually maintaining game names:

| Rule Type | Description | Example |
| --- | --- | --- |
| `path_keywords` | Install directory keywords; match = game | `steamapps`, `riot games`, `epic games` |
| `process_suffix` | Process name suffix characteristics | `-win64-shipping.exe` (Unreal Engine clients) |
| `process_exact` | Exact process name matches (lowercase, with `.exe`) | `valorant.exe`, `genshinimpact.exe` |
| `exclude_path_keywords` | Exclusion path keywords to prevent false positives | `netease`, `cloudmusic` |
| `exclude_process_exact` | Exclusion exact process name matches | `weixin.exe`, `qq.exe` |

Rules **append to the built-in list** by default with automatic deduplication. To completely replace the built-in list, set `replace_defaults` to `true`. Changes take effect after restarting the capture process.

### category_overrides.json

Manual category override format: `{ "process_name_lowercase": "app" | "game" }`, e.g. `{"cs2.exe": "app"}`. Classification priority: **Manual override > Game rules > Website > App**. Changes take effect **immediately** without restarting capture.

## Testing

No automated tests are available. Manual verification methods:
- Run `python main.py demo` to generate sample data, then open the GUI to check all pages
- Run `python main.py now` to verify foreground window identification
- Run `python main.py game rules` / `game check` to verify game identification rules
- Run `python main.py stats` to check console statistics output

## Contributing

1. Fork the repo
2. Create your feature branch (`git checkout -b feature/xxx`)
3. Commit your changes (`git commit -m 'feat: add xxx'`)
4. Push to the branch (`git push origin feature/xxx`)
5. Open a Pull Request

## License

This project is licensed under the [MIT](LICENSE) License.

## Contact

- GitHub: https://github.com/Qiongkura
- WeChat: Qiongkura

## Known Limitations

- Windows only (depends on Win32 API); macOS / Linux not supported
- `python main.py now` shows "No foreground window captured" in non-interactive desktop/sessions (remote sessions, scheduled tasks, services, etc.)
- Website identification depends on browser history databases; falls back to window title in private mode or when history is cleared
- Game identification rules are based on path/process name keywords; games not installed in rule-covered directories may need manual classification

## Related Projects

- [dsh-interface-settings](https://github.com/Qiongkura/dsh-interface-settings): Reference for README format conventions
