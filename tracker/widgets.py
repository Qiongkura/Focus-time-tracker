"""macOS 浅色风格控件库 + 进程图标/友好名称读取（无第三方硬依赖）。"""
from __future__ import annotations

import os
import time
import tkinter as tk
from pathlib import Path

from . import theme

try:  # Pillow（图标处理）
    from PIL import Image, ImageDraw, ImageTk
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageTk = None


def rounded_polygon(x1: float, y1: float, x2: float, y2: float, r: float) -> list:
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _darken(hex_color: str, factor: float) -> str:
    hex_color = (hex_color or "").lstrip("#")
    if len(hex_color) != 6:
        return hex_color
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * (1 - factor)))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# 进程友好名称 + 图标读取（优先 pywin32，缺失时纯 ctypes 兜底）
# --------------------------------------------------------------------------
_NAME_CACHE: dict = {}
_ICON_CACHE: dict = {}
_EXE_CACHE: dict = {}
_EXE_CACHE_TS = 0.0
_DEFAULT_ICON = None
_GLOBE_ICON = None
_ICON_ROOT = None
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
_BRAND_LOGO_CACHE: dict = {}

# 常见软件友好名称兜底（ProductName 缺失或与文件名相同时使用）
_ALIASES = {
    "douyin.exe": "抖音",
    "weixin.exe": "微信",
    "wechat.exe": "微信",
    "dingtalk.exe": "钉钉",
    "qq.exe": "QQ",
    "cloudmusic.exe": "网易云音乐",
    "wemeetapp.exe": "腾讯会议",
    "code.exe": "Visual Studio Code",
    "chrome.exe": "谷歌浏览器",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "explorer.exe": "文件资源管理器",
    "pycharm64.exe": "PyCharm",
    "doubao.exe": "豆包",
    "chatgpt.exe": "ChatGPT",
    "python.exe": "Python",
    "pythonw.exe": "Python",
    "cs2.exe": "Counter-Strike 2",
    "steam.exe": "Steam",
    "notepad.exe": "记事本",
    "cmd.exe": "命令提示符",
    "powershell.exe": "PowerShell",
    "windowsterminal.exe": "Windows Terminal",
}


def _icon_tk():
    """返回可用的 Tk 根窗口（GUI 启动后绑定到主窗口）。"""
    global _ICON_ROOT
    for cand in (_ICON_ROOT, tk._default_root):
        try:
            if cand is not None and cand.winfo_exists():
                return cand
        except Exception:
            continue
    try:
        _ICON_ROOT = tk.Tk()
        _ICON_ROOT.withdraw()
    except Exception:
        _ICON_ROOT = None
    return _ICON_ROOT


def get_exe_friendly_name(exe_path: str):
    """读取 exe 资源里的产品名称；失败返回 None（调用方回退）。"""
    if not exe_path:
        return None
    if exe_path in _NAME_CACHE:
        return _NAME_CACHE[exe_path]
    name = None
    try:
        import win32api  # pywin32 优先
        info = win32api.GetFileVersionInfo(exe_path, "\\")
        if isinstance(info, dict):
            for key in ("ProductName", "FileDescription"):
                if info.get(key):
                    name = info[key]
                    break
    except Exception:
        name = _friendly_name_ctypes(exe_path)
    if not name:
        name = _friendly_name_ctypes(exe_path)
    name = (name or "").strip() or None
    if name is not None and len(name) < 4 and not any(
            "\u4e00" <= ch <= "\u9fff" for ch in name):
        name = None  # 过短的英文名通常是资源截断的脏数据
    _NAME_CACHE[exe_path] = name
    return name


def _friendly_name_ctypes(exe_path: str):
    try:
        import ctypes
        from ctypes import wintypes
        version = ctypes.windll.version
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                                wintypes.DWORD, wintypes.LPVOID]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR,
                                           ctypes.POINTER(wintypes.LPVOID),
                                           ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(exe_path, 0, size, buf):
            return None

        def query_ptr(sub: str):
            ptr = ctypes.c_void_p()
            plen = wintypes.UINT()
            if not version.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(plen)):
                return None, 0
            return ptr.value, plen.value

        def query_str(sub: str):
            ptr, plen = query_ptr(sub)
            if not ptr:
                return ""
            # 某些 exe 的长度字段被截断，读到空字符为止
            chunk = ctypes.string_at(ptr, 1024)
            return chunk.decode("utf-16-le", "ignore").split("\x00")[0].strip()

        ptr, plen = query_ptr("\\VarFileInfo\\Translation")
        if ptr:
            trans = ctypes.string_at(ptr, plen)
            candidates = []
            for i in range(0, len(trans) - 3, 4):
                lang = trans[i] | (trans[i + 1] << 8)
                cp = trans[i + 2] | (trans[i + 3] << 8)
                for field in ("ProductName", "FileDescription"):
                    val = query_str(f"\\StringFileInfo\\{lang:04X}{cp:04X}\\{field}")
                    if val:
                        candidates.append(val)
            if candidates:
                return max(candidates, key=len)
    except Exception:
        pass
    return None


def _extract_icon_pywin32(exe_path: str, size: int):
    try:
        import win32gui
        import win32ui
        large, small = win32gui.ExtractIconEx(exe_path, 0)
        hicons = list(large) + list(small)
        if not hicons:
            return None
        try:
            hicon = hicons[0]
            w = h = max(48, size)  # 高分辨率提取再缩放，显示更清晰
            hdc = win32gui.GetDC(0)
            dc = win32ui.CreateDCFromHandle(hdc)
            mem = dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, w, h)
            old = mem.SelectObject(bmp)
            mem.DrawIcon((0, 0), hicon)
            bits = bmp.GetBitmapBits(True)
            mem.SelectObject(old)
            dc.DeleteDC()
            win32gui.ReleaseDC(0, hdc)
            img = Image.frombuffer("RGBA", (w, h), bits, "raw", "BGRA", 0, 1)
            return img.resize((size, size), Image.LANCZOS)
        finally:
            for h in hicons:
                try:
                    win32gui.DestroyIcon(h)
                except Exception:
                    pass
    except Exception:
        return None


def _extract_icon_ctypes(exe_path: str, size: int):
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        shell32 = ctypes.windll.shell32

        large = wintypes.HICON()
        small = wintypes.HICON()
        # ExtractIconExW 在 shell32.dll
        shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                           ctypes.POINTER(wintypes.HICON),
                                           ctypes.POINTER(wintypes.HICON), wintypes.UINT]
        shell32.ExtractIconExW.restype = wintypes.UINT
        count = shell32.ExtractIconExW(exe_path, 0, ctypes.byref(large),
                                       ctypes.byref(small), 1)
        if not count:
            return None
        hicon = small.value or large.value
        other = large.value if small.value else small.value
        if not hicon:
            return None
        try:
            w = h = max(48, size)  # 高分辨率提取再缩放，显示更清晰

            class BI(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                            ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD)]

            bmi = BI()
            bmi.biSize = ctypes.sizeof(BI)
            bmi.biWidth = w
            bmi.biHeight = -h  # top-down
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbmp:
                return None
            hdc = user32.GetDC(None)
            memdc = gdi32.CreateCompatibleDC(hdc)
            old = gdi32.SelectObject(memdc, hbmp)
            gdi32.PatBlt(memdc, 0, 0, w, h, 0x00000042)  # BLACKNESS
            user32.DrawIconEx(memdc, 0, 0, hicon, w, h, 0, None, 3)  # DI_NORMAL
            gdi32.SelectObject(memdc, old)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(None, hdc)
            data = ctypes.string_at(bits, w * h * 4)
            gdi32.DeleteObject(hbmp)
            img = Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", 0, 1)
            return img.resize((size, size), Image.LANCZOS)
        finally:
            user32.DestroyIcon(hicon)
            if other:
                user32.DestroyIcon(other)
    except Exception:
        return None


def _make_default_icon(size: int):
    """默认软件占位图标：蓝色圆角方块 + 内嵌浅色方块。"""
    if Image is None:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.28), fill=(0, 122, 255, 255))
    pad = int(s * 0.28)
    d.rounded_rectangle([pad, pad, s - 1 - pad, s - 1 - pad],
                        radius=int(s * 0.10), fill=(255, 255, 255, 235))
    return img


def _make_globe_icon(size: int):
    if Image is None:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    c = s // 2
    r = int(s * 0.42)
    d.ellipse([c - r, c - r, c + r, c + r], fill=(52, 199, 89, 255))
    d.ellipse([c - r, c - r, c + r, c + r], outline=(255, 255, 255, 255), width=max(1, s // 14))
    d.ellipse([c - r // 2, c - r, c + r // 2, c + r], outline=(255, 255, 255, 255), width=max(1, s // 14))
    d.line([c - r, c, c + r, c], fill=(255, 255, 255, 255), width=max(1, s // 14))
    return img


def get_default_icon(size: int = 24):
    global _DEFAULT_ICON
    if _DEFAULT_ICON is None and Image is not None:
        _DEFAULT_ICON = ImageTk.PhotoImage(_make_default_icon(size), master=_icon_tk())
    return _DEFAULT_ICON


def get_globe_icon(size: int = 24):
    global _GLOBE_ICON
    if _GLOBE_ICON is None and Image is not None:
        _GLOBE_ICON = ImageTk.PhotoImage(_make_globe_icon(size), master=_icon_tk())
    return _GLOBE_ICON


def get_brand_logo(size: int = 38):
    """加载项目自带的品牌 logo（透明背景 PNG），失败返回 None 由调用方兜底。"""
    if Image is None or not _LOGO_PATH.exists():
        return None
    size = int(size)
    if size in _BRAND_LOGO_CACHE:
        return _BRAND_LOGO_CACHE[size]
    try:
        img = Image.open(_LOGO_PATH).convert("RGBA")
        if img.width != size or img.height != size:
            img = img.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img, master=_icon_tk())
        _BRAND_LOGO_CACHE[size] = photo
        return photo
    except Exception:
        return None


def get_exe_icon(exe_path: str, size: int = 24):
    """返回 exe 内嵌图标（24x24 PhotoImage），失败返回默认图标。"""
    if not exe_path or Image is None:
        return get_default_icon(size)
    key = (exe_path, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = _extract_icon_pywin32(exe_path, size)
    if img is None:
        img = _extract_icon_ctypes(exe_path, size)
    if img is None:
        img = _make_default_icon(size)
    photo = ImageTk.PhotoImage(img, master=_icon_tk())
    _ICON_CACHE[key] = photo
    return photo


def clear_icon_caches():
    global _DEFAULT_ICON, _GLOBE_ICON, _ICON_CACHE
    _DEFAULT_ICON = None
    _GLOBE_ICON = None
    _ICON_CACHE.clear()


def set_icon_root(root):
    global _ICON_ROOT
    _ICON_ROOT = root
    clear_icon_caches()
    # 窗口标题栏/任务栏图标使用项目 logo
    try:
        photo = get_brand_logo(64)
        if photo is not None:
            root.iconphoto(True, photo)
    except Exception:
        pass


def get_display_name(process: str, exe_path: str) -> str:
    """展示名称优先级：有意义的 exe 产品名 → 内置别名 → 原始进程名。"""
    name = get_exe_friendly_name(exe_path) if exe_path else None
    if name:
        stem = os.path.splitext(os.path.basename(exe_path))[0].lower()
        if name.strip().lower() != stem:
            return name
    alias = _ALIASES.get((process or "").lower())
    if alias:
        return alias
    return name or (process or "")


def resolve_exe_path(process_name: str):
    """按进程名找正在运行的 exe 完整路径（Toolhelp 快照，10 秒缓存）。"""
    global _EXE_CACHE, _EXE_CACHE_TS
    if not process_name:
        return ""
    now = time.time()
    if now - _EXE_CACHE_TS > 10:
        _EXE_CACHE = {}
        _EXE_CACHE_TS = now
    if process_name in _EXE_CACHE:
        return _EXE_CACHE[process_name]
    path = _resolve_exe_ctypes(process_name)
    _EXE_CACHE[process_name] = path
    return path


def _resolve_exe_ctypes(process_name: str):
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return ""
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                return ""
            target = process_name.lower()
            while True:
                if entry.szExeFile.lower() == target:
                    return _query_path(entry.th32ProcessID)
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
            return ""
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return ""


def _query_path(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


# --------------------------------------------------------------------------
# 基础控件
# --------------------------------------------------------------------------
class RoundedCard(tk.Canvas):
    def __init__(self, master, radius: int = theme.CARD_RADIUS, fill: str = theme.CARD_BG,
                 outline=None, bg: str | None = None, shadow: bool = True,
                 hover: bool = True, **kw):
        super().__init__(master, bg=bg or theme.BG, highlightthickness=0, bd=0, **kw)
        self._radius = theme.scale(radius)
        self._fill = fill
        self._shadow = shadow
        self._hover_enabled = hover
        self._hover = False
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Map>", lambda _e: self._redraw(), add="+")
        if hover:
            self.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            self.bind("<Leave>", lambda _e: self._set_hover(False), add="+")

    def set_fill(self, fill: str):
        self._fill = fill
        self._redraw()

    def _set_hover(self, hover: bool):
        if self._hover != hover:
            self._hover = hover
            self._redraw()

    def _redraw(self, _event=None):
        self.delete("shape")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 4 or h <= 4:
            return
        if self._hover and self._hover_enabled:
            card = (0.5, 0.5, w - 0.5, h - 0.5)
            layers = [(2, 7, w - 2, h - 1, theme.CARD_SHADOW_HOVER), (2, 4, w - 2, h - 3, "#E7E7E7")]
        else:
            card = (1.5, 1.5, w - 1.5, h - 1.5)
            layers = [(3, 5, w - 3, h - 1, theme.CARD_SHADOW), (3, 3, w - 3, h - 3, "#EAEAEA")]
        if self._shadow:
            for x1, y1, x2, y2, color in layers:
                pts = rounded_polygon(x1, y1, x2, y2, self._radius)
                self.create_polygon(pts, smooth=True, fill=color, outline="", tags="shape")
        pts = rounded_polygon(*card, self._radius)
        self.create_polygon(pts, smooth=True, fill=self._fill, outline="", tags="shape")
        self.tag_lower("shape")


class NavButton(tk.Canvas):
    def __init__(self, master, text: str, icon: str, command=None,
                 width: int = 200, height: int = 38, bg: str = theme.SIDEBAR_BG):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         width=theme.scale(width), height=theme.scale(height))
        self._k = theme.scale(1.0)
        self._text = text
        self._icon = icon
        self._command = command
        self._selected = False
        self._hover = False
        self.bind("<Configure>", lambda _e: self._schedule_draw())
        self.bind("<Map>", lambda _e: self._schedule_draw())
        self.bind("<Button-1>", lambda _e: self._command() if self._command else None)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._draw()

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._draw()

    def _schedule_draw(self):
        try:
            self.after_idle(self._draw)
        except tk.TclError:
            pass

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        k = self._k
        if self._selected:
            fill, fg = theme.ACCENT_LIGHT, theme.ACCENT
        elif self._hover:
            fill, fg = "#DEDEE4", theme.TEXT
        else:
            fill, fg = theme.SIDEBAR_BG, theme.SUB
        pts = rounded_polygon(6 * k, 3 * k, w - 6 * k, h - 3 * k, 8 * k)
        self.create_polygon(pts, smooth=True, fill=fill, outline="")
        self.create_text(24 * k, h / 2, text=self._icon,
                         font=("Segoe UI Emoji", 12), fill=fg)
        self.create_text(36 * k, h / 2, text=self._text, anchor="w",
                         font=theme.font(11), fill=fg)


class RoundedButton(tk.Canvas):
    def __init__(self, master, text: str, command=None, width: int = 110,
                 height: int = 34, radius: int = theme.CONTROL_RADIUS,
                 fill: str = theme.ACCENT, fg: str = "#FFFFFF", bg: str = theme.BG,
                 font=None, hover: str | None = None, pressed: str | None = None,
                 selectable: bool = False, selected: bool = False, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         width=theme.scale(width), height=theme.scale(height), **kw)
        self._text = text
        self._command = command
        self._radius = theme.scale(radius)
        self._fill = fill
        self._fg = fg
        self._font = font or theme.font(10)
        self._hover_color = hover or _darken(fill, 0.05)
        self._pressed_color = pressed or _darken(fill, 0.12)
        self._selectable = selectable
        self._selected = selected
        self._hover = False
        self._pressed = False
        self.bind("<Configure>", lambda _e: self._schedule_draw())
        self.bind("<Map>", lambda _e: self._schedule_draw())
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def _press(self, _event):
        self._pressed = True
        self._draw()

    def _release(self, event):
        self._pressed = False
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._draw()
        if inside:
            if self._selectable:
                self._selected = True
                self._draw()
            if self._command:
                self._command()

    def set_selected(self, selected: bool):
        if self._selectable:
            self._selected = selected
            self._draw()

    def set_text(self, text: str):
        self._text = text
        self._draw()

    def _set_hover(self, hover: bool):
        self._hover = hover
        if not hover:
            self._pressed = False
        self._draw()

    def _schedule_draw(self):
        try:
            self.after_idle(self._draw)
        except tk.TclError:
            pass

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self._selectable and not self._selected:
            fill, fg = theme.SECONDARY_BG, theme.TEXT
            hover, pressed = theme.SECONDARY_HOVER, theme.SECONDARY_PRESSED
        else:
            fill, fg = self._fill, self._fg
            hover, pressed = self._hover_color, self._pressed_color
        if self._pressed:
            fill = pressed
        elif self._hover:
            fill = hover
        pts = rounded_polygon(1, 1, w - 1, h - 1, self._radius)
        self.create_polygon(pts, smooth=True, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, font=self._font, fill=fg)


class ProgressBar(tk.Canvas):
    def __init__(self, master, width: int = 180, height: int = 9,
                 radius: int = theme.CONTROL_RADIUS,
                 track: str = theme.TRACK_BG, fill: str = theme.ACCENT, **kw):
        super().__init__(master, bg=master.cget("bg") if isinstance(master, tk.Widget) else theme.BG,
                         highlightthickness=0, bd=0,
                         width=theme.scale(width), height=theme.scale(height), **kw)
        self._track = track
        self._fill = fill
        self._radius = theme.scale(radius)
        self._ratio = 0.0
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, ratio: float):
        self._ratio = max(0.0, min(1.0, ratio))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        r = min(self._radius, h / 2)
        pts = rounded_polygon(1, 1, w - 1, h - 1, r)
        self.create_polygon(pts, smooth=True, fill=self._track, outline="")
        if self._ratio > 0.005:
            fw = max(3.0, (w - 2) * self._ratio)
            fpts = rounded_polygon(1, 1, 1 + fw, h - 1, r)
            self.create_polygon(fpts, smooth=True, fill=self._fill, outline="")


class ScrollableFrame(tk.Frame):
    def __init__(self, master, bg: str = theme.BG, **kw):
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._win, width=e.width))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ScrollArea(tk.Frame):
    """横向 + 纵向双滚动容器：内容宽度 = max(画布可视宽度, 内容最小宽度)。"""

    def __init__(self, master, bg: str = theme.BG, min_width: int = 620,
                 respect_req: bool = True, **kw):
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        vsb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                           bg="#D9D9DE", troughcolor=bg, bd=0, highlightthickness=0, relief="flat")
        hsb = tk.Scrollbar(self, orient="horizontal", command=self.canvas.xview,
                           bg="#D9D9DE", troughcolor=bg, bd=0, highlightthickness=0, relief="flat")
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._min_width = theme.scale(min_width)
        self._respect_req = respect_req
        self.vsb = vsb
        self.hsb = hsb
        self._syncing = False
        self._v_on = True
        self._h_on = True
        self._sync_pending = False
        self.inner.bind("<Configure>", lambda _e: self._schedule_sync())
        self.canvas.bind("<Configure>", lambda e: self._resize_content(e.width))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self._sync_timer = None
        self._periodic_sync()

    def _schedule_sync(self):
        """防抖滚动同步：批量创建子控件时避免每个 Configure 都重算 bbox。"""
        if self._sync_pending:
            return
        self._sync_pending = True
        try:
            self.after(80, self._run_sync)
        except tk.TclError:
            self._sync_pending = False

    def _run_sync(self):
        self._sync_pending = False
        try:
            self.refresh_scroll()
        except tk.TclError:
            pass

    def _periodic_sync(self):
        try:
            if not self.winfo_exists():
                return
            self.refresh_scroll()
        except tk.TclError:
            return
        self._sync_timer = self.after(400, self._periodic_sync)

    def refresh_scroll(self):
        """刷新滚动区域并决定滚动条显隐。"""
        if self._syncing:
            return
        self._syncing = True
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            y0, y1 = self.canvas.yview()
            x0, x1 = self.canvas.xview()
            need_v = not (y0 <= 0.0 and y1 >= 1.0)
            need_h = not (x0 <= 0.0 and x1 >= 1.0)
            # 只有状态真正变化才切换滚动条，避免无限重排级联
            if need_v != self._v_on:
                self._v_on = need_v
                if need_v:
                    self.vsb.grid()
                else:
                    self.vsb.grid_remove()
            if need_h != self._h_on:
                self._h_on = need_h
                if need_h:
                    self.hsb.grid()
                else:
                    self.hsb.grid_remove()
        finally:
            self._syncing = False

    def _resize_content(self, canvas_width: int):
        try:
            if self._respect_req:
                req = self.inner.winfo_reqwidth()
                width = max(int(canvas_width), int(req), int(self._min_width))
            else:
                # 不强制保持内容最小宽度，允许内容随窗口变窄重排（如卡片竖排）
                width = max(int(canvas_width), int(self._min_width))
            self.canvas.itemconfig(self._win, width=width)
            self._schedule_sync()
        except tk.TclError:
            pass

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_shift_wheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120), "units")


class FlowFrame(tk.Frame):
    """栅格流式布局：子控件按固定宽度自动换行。"""

    def __init__(self, master, bg: str = theme.BG, item_width: int = 210,
                 item_height: int = 92, gap: int = 12, **kw):
        super().__init__(master, bg=bg, **kw)
        self._item_width = theme.scale(item_width)
        self._item_height = theme.scale(item_height)
        self._gap = theme.scale(gap)
        self._children = []
        self.bind("<Configure>", self._relayout)

    def add(self, widget):
        widget.pack_forget()
        self._children.append(widget)
        self._relayout()

    def _relayout(self, _event=None):
        avail = self.winfo_width()
        if avail < 100:
            max_cols = 1
        else:
            max_cols = int(max(1, (avail + self._gap) // (self._item_width + self._gap)))
        for w in self._children:
            w.grid_forget()
        row = 0
        col = 0
        for w in self._children:
            w.grid(row=row, column=col, padx=self._gap // 2, pady=self._gap // 2, sticky="nsew")
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        # 只给实际有卡片的列配权重，避免空列把卡片挤窄导致内容被裁
        for c in range(min(max_cols, len(self._children))):
            self.grid_columnconfigure(c, weight=1, uniform="flow")
        for r in range(row + (1 if col else 0)):
            self.grid_rowconfigure(r, weight=0)
