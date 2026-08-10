"""macOS 浅色原生风格配色与字体（全局唯一设计令牌）。"""
# 背景与表面
BG = "#F5F5F7"                # 主内容画布
SIDEBAR_BG = "#E8E8ED"        # 侧边栏
SIDEBAR_WIDTH = 160           # 侧边栏固定宽度（禁止自适应拉伸）
CARD_BG = "#FFFFFF"           # 卡片
# 卡片投影：12% 黑色微弱投影的近似分层色，无硬边框
CARD_SHADOW = "#DCDCDC"
CARD_SHADOW_HOVER = "#CFCFCF"
# 主强调色：苹果系统蓝
ACCENT = "#007AFF"
ACCENT_HOVER = "#0069E3"      # 悬浮 -5%
ACCENT_PRESSED = "#005AC7"    # 按下 -12%
ACCENT_LIGHT = "#D8E4F6"      # 导航选中整块填充
ACCENT_LIGHTER = "#EAF3FF"    # 浅蓝卡片底（网站卡）
# 文字三层
TEXT_TITLE = "#1D1D1F"        # 页面大标题
TEXT = "#39393D"              # 普通正文
SUB = "#86868B"               # 备注/次要小字
# 控件
TRACK_BG = "#ECECEC"          # 进度条轨道
SECONDARY_BG = "#E9E9EB"      # 普通灰色按钮
SECONDARY_HOVER = "#DFDFE2"
SECONDARY_PRESSED = "#D5D5D9"
# 圆角（全局固定）
CARD_RADIUS = 16
CONTROL_RADIUS = 6
CATEGORY_META = {
    "应用": ("📦", "#0A84FF"),
    "游戏": ("🎮", "#AF52DE"),
    "网站": ("🌐", "#34C759"),
    "系统": ("⚙️", "#FF9F0A"),
}
MINI_CARDS = [
    ("#E8F1FF", "#0066CC"),
    ("#E5F6EC", "#248A3D"),
    ("#F0E8FF", "#8944AB"),
    ("#FFF2E5", "#C25E00"),
]
_FONT_FAMILY = "Microsoft YaHei UI"
_FONT_CANDIDATES = ("San Francisco", "SF Pro Text", "SF Pro Display",
                    "Microsoft YaHei UI", "Microsoft YaHei")
_SCALE = 1.0

def font(size: int = 10, bold: bool = False):
    return (_FONT_FAMILY, size, "bold" if bold else "normal")

def set_scale(factor: float):
    global _SCALE
    _SCALE = factor if factor and factor > 0 else 1.0

def scale(value) -> float:
    return value * _SCALE

def init_font(root=None):
    global _FONT_FAMILY
    try:
        import tkinter.font as tkfont
        if root is None:
            import tkinter as tk
            root = tk._default_root
        if root is None:
            return
        installed = set(tkfont.families(root))
        for name in _FONT_CANDIDATES:
            if name in installed:
                _FONT_FAMILY = name
                return
    except Exception:
        pass