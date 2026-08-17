# Focus-time-tracker（屏幕使用时间统计工具）

<div align="center">

**中文** | [English](README.en.md)

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Star](https://img.shields.io/github/stars/Qiongkura/Focus-time-tracker.svg)](https://github.com/Qiongkura/Focus-time-tracker/stargazers)
[![Issues](https://img.shields.io/github/issues/Qiongkura/Focus-time-tracker.svg)](https://github.com/Qiongkura/Focus-time-tracker/issues)

</div>

一个用 Python 编写的 Windows 桌面工具，自动检测当前聚焦的前台窗口/进程，记录使用时长，并用浅色风格的界面可视化展示。

- **前台窗口采集**：通过 Win32 API 采样当前前台窗口（仅用标准库 `ctypes`），识别桌面软件、游戏、浏览器、桌面/锁屏并自动分类；
- **游戏识别规则引擎**：按"启动器/安装目录"关键字 + 进程名规则自动识别游戏，支持 Steam / Riot / Epic / 战网 / WeGame / 米哈游等平台，规则可在 `game_rules.json` 里增删；
- **具体网站识别**：浏览器窗口标题 + 浏览器历史库（SQLite）匹配出当前正在看的网站，支持 Chrome / Edge / Firefox，全程本地匹配，不联网、不上传任何数据。

## 功能

| 功能 | 说明 |
| --- | --- |
| 前台窗口采集 | 通过 Win32 API 采样当前前台窗口，自动识别并分类桌面软件、游戏、浏览器、桌面/锁屏 |
| 游戏识别规则引擎 | 按"启动器/安装目录"关键字 + 进程名规则自动识别游戏，无需逐个维护游戏名 |
| 具体网站识别 | 浏览器窗口标题 + 浏览器历史库匹配出当前正在看的网站，支持 Chrome / Edge / Firefox |
| 网站自动去重 | 多标签窗口标题里的噪音后缀会被清洗，同一网站自动合并统计 |
| 会话记录 | 按"进程 / 网站"累计时长，存入 SQLite（WAL 模式，支持 GUI 读 + 后台写并发） |
| 浅色 GUI | 首页（概览）、统计、详细记录、分类、设置五个页面，左侧导航栏 + 右侧内容区 |
| 统计图表 | 今日每小时使用柱状图、近 7 天分类趋势图（圆角柱、平均线），随窗口大小自适应缩放 |
| 详细记录按小时合并 | 同一小时内同一进程/网站合并为一行，显示时间区间与总时长、出现次数 |
| 分类页全量展示 | 应用 / 游戏 / 网站三张卡片，显示该分类全部进程/网站，每项带时长与占分类总时长百分比 |
| 手动分类覆盖 | 分类页每个进程行带「移到游戏 / 移到应用」按钮，点击即永久归到目标分类，立即生效 |
| 系统托盘常驻 | 最小化到托盘继续记录，托盘菜单可恢复主界面 / 退出 |
| 互斥采集 | `dashboard` 与 `start` 共用同一把锁，保证同时只有一个采集进程写库，避免时长重复统计 |
| 实时刷新 | 每 2 秒刷新首页、分类、设置页数据 |

## 架构设计

- **采集层**（`tracker/monitor.py`）：Win32 API 轮询前台窗口，每 2 秒采样一次，识别窗口进程、标题、路径，调用分类引擎判定类别，写入 SQLite
- **分类引擎**（`tracker/games.py`）：规则引擎优先级为「手动覆盖 > 游戏规则 > 网站 > 应用」，规则支持 path_keywords / process_suffix / process_exact / 排除规则
- **浏览器识别**（`tracker/browser.py`）：匹配 Chrome / Edge / Firefox 的历史数据库（SQLite），提取当前正在访问的网站域名
- **存储层**（`tracker/db.py`）：SQLite WAL 模式，支持 GUI 并发读取和后台写入；按天自动拆分跨午夜会话
- **GUI 层**（`tracker/app.py`）：五个页面（首页/统计/详细记录/分类/设置），浅色主题，圆角控件，左侧导航栏
- **系统托盘**（`tracker/tray.py`）：最小化到托盘继续记录，支持恢复主界面和退出

## 📦 环境依赖

```bash
Windows 10 / 11（依赖 Win32 API，不支持 macOS / Linux）
Python 3.10+（使用了 str | None 等新语法）
```

依赖包：
- `matplotlib>=3.5`（图表）
- `Pillow`（图标与 Logo 显示；缺失时图标回退为默认占位）

## 安装与使用

```bash
cd focus-time-tracker
python -m pip install -r requirements.txt
python -m pip install pillow        # 建议安装，用于图标 / Logo
```

> **注意**：`dashboard` 会自动拉起独立后台采集进程，`start` 也会启动采集，两者互斥，**不要同时运行**，否则会重复统计。

命令一览：

| 命令 | 说明 |
| --- | --- |
| `python main.py dashboard` | 打开可视化界面（自动开始后台采集） |
| `python main.py start` | 后台采集（前台循环，Ctrl+C 停止） |
| `python main.py stats` | 控制台打印今日统计 |
| `python main.py report` | 生成今日报告 PNG |
| `python main.py report --days N` | 生成近 N 天趋势图 |
| `python main.py now` | 查看当前前台窗口信息（诊断用） |
| `python main.py game rules` | 打印当前生效的游戏识别规则 |
| `python main.py game check --path "C:\Riot Games\VALORANT\live\VALORANT.exe" --process VALORANT.exe` | 按规则判断某个进程/路径是否为游戏 |
| `python main.py unlock` | 清除残留采集锁 |
| `python main.py demo` | 生成 7 天示例数据，便于预览 |

## 📝 使用示例

```bash
# 启动 GUI 并自动采集
python main.py dashboard

# 仅后台采集，不开界面
python main.py start

# 查看今日统计
python main.py stats

# 生成 7 天趋势图
python main.py report --days 7

# 诊断：查看当前前台窗口
python main.py now

# 测试游戏识别规则
python main.py game check --process VALORANT.exe --path "C:\Riot Games\VALORANT\live\VALORANT.exe"

# 生成示例数据预览效果
python main.py demo
```

## ⚙️ 配置说明

### config.json

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `poll_interval_seconds` | 采样间隔（秒），调小更精确、调大更省资源 | `1.0` |
| `min_session_seconds` | 短于该时长的窗口切换不记录，避免碎片数据 | `3` |
| `exclude_processes` | 不想统计的进程名，如 `["explorer.exe"]` | `["python.exe"]` |
| `browser_site_tracking` | 是否识别浏览器正在访问的具体网站；关闭后浏览器按普通应用统计 | `true` |
| `data_dir` | 数据目录（相对项目根目录，也可写绝对路径） | `"data"` |
| `report_dir` | 报告目录（相对项目根目录，也可写绝对路径） | `"reports"` |

### game_rules.json

游戏分类按"规则引擎"判断，新增平台/游戏基本不用改代码：

| 规则类型 | 说明 | 示例 |
| --- | --- | --- |
| `path_keywords` | 安装目录关键字，命中即算游戏 | `steamapps`、`riot games`、`epic games` |
| `process_suffix` | 进程名后缀特征 | `-win64-shipping.exe`（Unreal 引擎客户端） |
| `process_exact` | 进程名精确匹配（小写、带 `.exe`） | `valorant.exe`、`genshinimpact.exe` |
| `exclude_path_keywords` | 排除路径关键字，防止误伤普通软件 | `netease`、`cloudmusic` |
| `exclude_process_exact` | 排除进程名精确匹配 | `weixin.exe`、`qq.exe` |

规则默认**向内置列表追加**并自动去重；若想彻底替换内置列表，把 `replace_defaults` 改为 `true`。改完 `game_rules.json` 后重启采集进程生效。

### category_overrides.json

手动分类覆盖格式：`{ "进程名小写": "应用" | "游戏" }`，例如 `{"cs2.exe": "应用"}`。分类优先级：**手动覆盖 > 游戏规则 > 网站 > 应用**，改完**立即生效**，无需重启采集。

## 🧪 测试

暂无自动化测试。可通过以下方式手动验证：
- 执行 `python main.py demo` 生成示例数据，打开 GUI 检查各页面显示
- 执行 `python main.py now` 检查当前前台窗口识别是否正常
- 执行 `python main.py game rules` / `game check` 验证游戏识别规则
- 执行 `python main.py stats` 检查控制台统计输出

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建你的功能分支 (`git checkout -b feature/xxx`)
3. 提交你的修改 (`git commit -m 'feat: 新增xxx功能'`)
4. 推送到分支 (`git push origin feature/xxx`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证。

## 📮 联系方式

- GitHub：https://github.com/Qiongkura
- 微信：Qiongkura

## 已知限制

- 仅支持 Windows（依赖 Win32 API），不支持 macOS / Linux
- `python main.py now` 在非交互桌面/会话（远程会话、计划任务、服务等）下会显示"未获取到前台窗口"
- 网站识别依赖浏览器历史库，在隐私模式或历史被清空时会退化为用窗口标题展示
- 游戏识别规则基于路径/进程名关键字，未安装在规则覆盖目录的游戏可能需要手动分类

## 与相关项目的关系

- [dsh-interface-settings](https://github.com/Qiongkura/dsh-interface-settings)：参考其 README 格式规范
