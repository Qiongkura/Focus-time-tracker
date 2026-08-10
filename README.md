# 屏幕使用时间统计工具

一个用 Python 编写的 Windows 小工具：**自动检测当前屏幕聚焦的前台窗口/进程，记录使用时长，并用图表可视化**。
界面采用 Tai 风格：纯白扁平化、圆角卡片、柔和薄荷绿主题。

## 功能

- 前台窗口检测：通过 Win32 API 每秒采样一次当前前台窗口（零第三方依赖，仅用标准库 `ctypes`）
- **具体网站识别**：使用浏览器时，通过「窗口标题 + 浏览器历史库」匹配出当前正在看的网站（支持 Chrome / Edge / Firefox）
- 自动分类：桌面软件（应用）、Steam 游戏、浏览器网页（网站）、桌面/锁屏（系统）分开统计
- 会话记录：按“进程 / 网站”累计时长，数据存入 SQLite（`data/usage.db`）
- **Tai 风格 GUI**：左侧导航（首页 / 统计 / 详细记录 / 分类 / 设置）+ 右侧内容区；首页有今日/本周切换、应用与网站双卡片、彩色缩略卡；打开界面即自动开始追踪
- 图表：今日 Top 应用条形图 + 占比饼图、近 7 天趋势堆叠柱状图
- 实时刷新：每 2 秒自动刷新当前窗口、今日统计与进度条
- 控制台统计：命令行直接查看今日时长排名
- 特殊场景识别：桌面、锁屏、最小化窗口会自动归类

## 运行要求

- Windows 10/11（依赖 Win32 API，不支持 macOS / Linux）
- Python 3.8+
- 安装依赖：`pip install -r requirements.txt`

## 快速开始

```bash
cd focus-time-tracker
pip install -r requirements.txt

python main.py dashboard        # 1. 打开 Tai 风格可视化界面（自动开始追踪）
python main.py start            # 2. 或不用界面，只在后台记录（Ctrl+C 停止）
python main.py report           # 3. 生成今日报告图片（PNG）
python main.py report --days 7  # 4. 生成近 7 天趋势图
python main.py stats            # 5. 在控制台查看今日统计
```

> 注意：`dashboard` 和 `start` 各自会开启一个追踪循环，请**不要同时运行**，否则会重复记录。

想先看看图表长什么样？执行 `python main.py demo` 生成 7 天示例数据（会混入现有数据，介意的话先备份 `data/usage.db`），再执行 `python main.py report --days 7`。

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `python main.py dashboard` | 打开 Tai 风格可视化界面（自动开始追踪） |
| `python main.py start` | 后台记录（前台循环，Ctrl+C 停止） |
| `python main.py report` | 生成今日 Top 应用图 |
| `python main.py report --days 7` | 生成近 7 天趋势图（天数可改） |
| `python main.py stats` | 控制台打印今日统计 |
| `python main.py unlock` | 清除残留的采集锁文件（提示“已有采集会话在运行”时使用） |
| `python main.py now` | 查看当前前台窗口信息（诊断用） |
| `python main.py demo` | 生成 7 天示例数据，便于预览 |

## GUI 页面说明

- **首页（概览）**：今日 / 本周切换标签；「最为频繁」区左右并排两张卡片——左「应用」（灰色进度条）、右「网站」（绿色进度条），每行 = 图标 + 名称 + 进度条 + 时长；下方分类下拉框 + 4 张彩色缩略卡
- **统计**：今日应用 Top 条形图、今日分类占比饼图、近 7 天分类趋势图
- **详细记录**：当天全部会话的时间段、名称、分类、时长列表
- **分类**：应用 / 游戏 / 网站三张卡片，含总时长、条数、最常用与占比
- **设置**：采样间隔、最短会话、排除进程、网站识别开关、追踪启停、数据目录、当前前台窗口

## 数据与报告位置

- 数据库：`data/usage.db`（SQLite，可直接用工具查看）
- 报告图片：`reports/` 目录下的 PNG
- 想清空记录重新开始：删除 `data/usage.db`（及旁边的 `-wal` / `-shm` 文件）即可

## 配置（config.json）

```json
{
  "poll_interval_seconds": 1.0,
  "min_session_seconds": 3,
  "exclude_processes": [],
  "browser_site_tracking": true,
  "data_dir": "data",
  "report_dir": "reports"
}
```

- `poll_interval_seconds`：采样间隔（秒），调小更精确、调大更省资源
- `min_session_seconds`：短于该时长的窗口切换不记录，避免碎片数据
- `exclude_processes`：不想统计的进程名，如 `["explorer.exe", "LockApp.exe"]`；处于这些进程时不累计任何应用时长
- `browser_site_tracking`：是否识别浏览器正在访问的具体网站；关闭后浏览器按普通应用统计
- `data_dir` / `report_dir`：数据与报告目录（相对项目根目录，也可写绝对路径）

## 进阶：开机自动记录

1. 按 `Win + R` 输入 `shell:startup` 打开启动文件夹
2. 新建一个快捷方式，目标填：
   `pythonw.exe 的完整路径 C:\...\focus-time-tracker\main.py start`
   例如：`C:\Python311\pythonw.exe C:\...\focus-time-tracker\main.py start`
3. 开机后它会在后台静默记录，之后用 `dashboard` / `report` 查看即可

## 常见问题

- **网站是怎么识别出来的？**：浏览器窗口标题通常就是当前页面标题，程序把标题与浏览器历史库（SQLite）里最近访问的记录做匹配，得到站点域名和完整 URL。整个过程只在本地进行，不联网、不上传任何数据。隐私模式、历史被清空时会退化为用窗口标题展示。
- **PyCharm/虚拟环境提示“不满足软件包要求 'matplotlib>=3.5'”**：多半是 IDE 创建的虚拟环境（`.venv`）里没有 matplotlib，而 pip 安装又被网络/防火墙拦截。解决办法：
  1. 最简单：PyCharm 里 `File → Settings → Project → Python Interpreter`，把解释器换成系统 Python（如 `I:\python\python.exe`），它已自带 matplotlib；
  2. 或保留虚拟环境：能联网时在虚拟环境里执行 `python -m pip install -r requirements.txt`；
  3. 离线环境：把系统 Python `Lib\site-packages` 里的 `matplotlib`、`numpy`、`PIL`、`contourpy`、`cycler`、`fontTools`、`kiwisolver`、`packaging`、`pyparsing`、`dateutil`、`six`、`mpl_toolkits` 及对应 `.dist-info` 目录复制到 `.venv\Lib\site-packages`（Python 版本一致时可直接通用）。
- **`python main.py now` 显示“未获取到前台窗口”**：说明当前进程运行在非交互桌面/会话里（如某些远程会话、计划任务、服务）。请在你本人的正常登录桌面终端里运行。
- **跨天会话**：跨午夜的长会话会自动按天拆分，归属各自日期。

## 项目结构

```text
focus-time-tracker/
├── main.py                 # 命令行入口
├── config.json             # 配置
├── requirements.txt
├── tracker/
│   ├── monitor.py          # Win32 前台窗口采集 + 记录主循环
│   ├── db.py               # SQLite 存储与聚合
│   ├── browser.py          # 浏览器当前网站识别（Chrome/Edge/Firefox）
│   ├── report.py           # matplotlib 图表
│   ├── app.py              # Tai 风格 GUI（5 个页面）
│   ├── widgets.py          # 圆角控件库
│   ├── theme.py            # 配色与字体
│   ├── demo.py             # 示例数据
│   └── utils.py            # 时长格式化
├── data/                   # 运行时生成（SQLite）
└── reports/                # 运行时生成（PNG 图表）
```
