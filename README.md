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
| 长会话 checkpoint | 长会话每隔 `checkpoint_seconds` 落盘一段，进程被强杀/断电时最多丢这一段 |
| 浅色 GUI | 首页（概览）、统计、详细记录、分类、设置五个页面，左侧导航栏 + 右侧内容区 |
| 统计图表 | 今日每小时使用柱状图、近 7 天分类趋势图（圆角柱、平均线），随窗口大小自适应缩放 |
| 详细记录按小时合并 | 同一小时内同一进程/网站合并为一行，显示时间区间与总时长、出现次数 |
| 分类页全量展示 | 应用 / 游戏 / 网站三张卡片，显示该分类全部进程/网站，每项带时长与占分类总时长百分比 |
| 手动分类覆盖 | 分类页每个进程行带「移到游戏 / 移到应用」按钮，点击即永久归到目标分类，立即生效 |
| 系统托盘常驻 | 最小化到托盘继续记录，托盘菜单可恢复主界面 / 退出 |
| 互斥采集 | `dashboard` 与 `start` 共用同一把锁，保证同时只有一个采集进程写库，避免时长重复统计 |
| 实时刷新 | 每 2 秒刷新首页、分类、设置页数据（同一时间窗只查一次库） |

## 架构设计

- **采集层**（`tracker/monitor.py`）：按职责拆成 `WindowSampler`（取前台窗口）、`SessionAccumulator`（切段与 checkpoint）、`SessionWriter`（落库）、`TrackingService`（编排主循环）；模块级 `run_tracking` / `get_foreground_info` 接口保持不变
- **分类引擎**（`tracker/games.py`）：规则引擎优先级为「手动覆盖 > 游戏规则 > 网站 > 应用」，规则支持 path_keywords / process_suffix / process_exact / 排除规则
- **浏览器识别**（`tracker/browser.py`）：匹配 Chrome / Edge / Firefox 的历史数据库（SQLite），提取当前正在访问的网站域名
- **存储层**（`tracker/db.py`）：SQLite WAL 模式，支持 GUI 并发读取和后台写入；按天自动拆分跨午夜会话；`metadata` 表记录 schema 版本，分类回填只在版本升级或规则文件变化时执行
- **互斥采集**（`tracker/lock.py`）：`os.open(O_CREAT | O_EXCL)` 原子抢锁，锁操作失败时 fail-closed（拒绝启动而不是放行），并校验进程创建时间以防 PID 复用误判
- **隐私与保留**（`tracker/privacy.py`）：URL 落库策略（剥离 query/fragment、可选只存域名）与历史保留天数清理
- **GUI 层**（`tracker/app.py` + `tracker/ui/`）：五个页面（首页/统计/详细记录/分类/设置），浅色主题，圆角控件，左侧导航栏。`app.py` 只做组装入口，页面与基础设施按职责拆在 `tracker/ui/` 下：

  | 模块 | 职责 |
  | --- | --- |
  | `app_shell.py` | 窗口骨架：DPI、样式、侧边栏、页面容器、日志轮转、生命周期 |
  | `home_page.py` | 首页概览卡片与时段切换 |
  | `stats_page.py` | 统计页（matplotlib 懒加载，自定义圆角柱） |
  | `records_page.py` | 详细记录页（按小时合并 + 分批流式渲染） |
  | `categories_page.py` | 分类页（三栏布局 + 进程改判） |
  | `settings_page.py` | 设置页（采样参数 / 排除进程 / 数据文件） |
  | `refresh_controller.py` | 刷新调度与跨 0 点提示 |
  | `background.py` | 后台采集子进程、系统托盘、启动期自检 |
  | `preview.py` | 前台窗口预览采样线程 |
  | `common.py` | 跨页面共用的纯展示函数 |

  各页面以 mixin 形式由 `ScreenTimeApp` 多继承组装，彼此只通过 `self` 交换状态，因此拆分不改变任何对外调用点。
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
| `python main.py doctor` | 自检：残留锁 / 重叠记录 / 数据库体积 / 隐私策略 |
| `python main.py unlock` | 清除残留采集锁 |
| `python main.py backup [--out 路径]` | 备份数据库（含 WAL 中的最新数据） |
| `python main.py restore 备份文件 --force` | 从备份恢复数据库（覆盖当前数据） |
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
| `poll_interval_seconds` | 采样间隔（秒），调小更精确、调大更省资源；低于 `0.2` 会被自动提到 `0.2` | `1.0` |
| `min_session_seconds` | 短于该时长的窗口切换不记录，避免碎片数据 | `3` |
| `checkpoint_seconds` | 长会话每隔该秒数落盘一段，进程被强杀/断电时最多丢这一段 | `45` |
| `exclude_processes` | 不想统计的进程名列表，如 `["explorer.exe"]`；**大小写不敏感** | `[]` |
| `browser_site_tracking` | 是否识别浏览器正在访问的具体网站；关闭后浏览器按普通应用统计 | `true` |
| `data_dir` | 数据目录（相对项目根目录，也可写绝对路径） | `"data"` |
| `report_dir` | 报告目录（相对项目根目录，也可写绝对路径） | `"reports"` |
| `retention_days` | 历史保留天数，`0` 表示永久保留；采集启动时清理更早的记录并 VACUUM | `0` |
| `store_full_url` | 是否保存完整 URL；设为 `false` 时只保存 `协议://域名` | `true` |
| `strip_url_query` | 保存 URL 时去掉 `?query` 与 `#fragment`，避免搜索词、临时 token 落盘 | `true` |

配置项都有类型与取值校验：写错的字段会被就地修正（例如 `poll_interval_seconds: 0`
会被提到 `0.2`，`exclude_processes` 写成字符串会被规整成列表），问题会写进
`data/ui_errors.log`，不会让程序启动失败。`config.json` 未写出的项一律使用上表默认值。

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

自动化测试（游戏规则、站点清洗、DB 聚合、浏览器缓存、采集锁原子性、采集组件切段、
配置校验、URL 隐私策略、GUI 页面 mixin 契约等，共 180 例）：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Windows CI（`.github/workflows/ci.yml`）会在 Python 3.10 / 3.12 上跑 `compileall`
语法检查与全部单测。注意 `tracker/monitor.py` 依赖 Win32 API，在非 Windows 平台
导入时即抛错，所以 CI 必须使用 `windows-latest`。

`tests/test_ui_modules.py` 里有一项静态名字解析检查：它扫描 `tracker/ui/` 下每个
方法体（含注解），确认引用的名字都能在模块顶层或函数作用域里找到。拆 GUI 时最容易
犯的错误是「搬过来的代码调用了没导入的名字」——这类问题只在对应页面真的被构建时才
抛 `NameError`，单测通常不建 Tk 窗口，所以会静默通过。

手动验证：
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
