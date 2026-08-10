"""系统托盘图标（任务栏右下角通知区）——纯标准库 ctypes 实现，无第三方依赖。

用 Win32 Shell_NotifyIcon + 消息窗口实现托盘常驻：
- 左键单击 / 双击：恢复主界面
- 右键：弹出菜单（打开主界面 / 退出）
- 托盘图标为运行时生成的 .ico：蓝色圆角方块 + 白色时钟
"""
from __future__ import annotations

import ctypes
import math
import struct
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# 常量
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_NULL = 0
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
WM_LBUTTONUP = 0x202
WM_LBUTTONDBLCLK = 0x203
WM_RBUTTONUP = 0x205
HWND_MESSAGE = -3
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
ID_OPEN = 1001
ID_QUIT = 1002


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]

_state = {
    "hwnd": None,
    "wndproc": None,
    "nid": None,
    "hicon": None,
    "hinst": None,
    "on_open": None,
    "on_quit": None,
    "class_name": "ScreenTimeTrayMsgWnd_A1B2C3",
}


def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAY:
        code = lparam & 0xFFFF
        if code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            if _state["on_open"]:
                _state["on_open"]()
            return 0
        if code == WM_RBUTTONUP:
            _show_menu(hwnd)
            return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_INSTANCE = WNDPROC(_wnd_proc)


def _show_menu(hwnd):
    menu = user32.CreatePopupMenu()
    if not menu:
        return
    user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "打开主界面")
    user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出")
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)
    cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                pt.x, pt.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    user32.DestroyMenu(menu)
    if cmd == ID_OPEN and _state["on_open"]:
        _state["on_open"]()
    elif cmd == ID_QUIT and _state["on_quit"]:
        _state["on_quit"]()


def enable_tray(icon_path: str, on_open=None, on_quit=None, tooltip: str = "") -> bool:
    """添加系统托盘图标。返回是否成功；失败时调用方应回退为任务栏最小化。"""
    if _state["hwnd"]:
        return True
    _state["on_open"] = on_open
    _state["on_quit"] = on_quit

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _WNDPROC_INSTANCE
    wc.hInstance = hinst
    wc.lpszClassName = _state["class_name"]
    if not user32.RegisterClassW(ctypes.byref(wc)):
        return False

    hwnd = user32.CreateWindowExW(0, _state["class_name"], "", 0,
                                  0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
                                  None, hinst, None)
    if not hwnd:
        user32.UnregisterClassW(_state["class_name"], hinst)
        return False

    hicon = user32.LoadImageW(None, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
    if not hicon:
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(_state["class_name"], hinst)
        return False

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = hicon
    nid.szTip = (tooltip or "屏幕使用时间")[:127]

    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        user32.DestroyIcon(hicon)
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(_state["class_name"], hinst)
        return False

    _state.update(hwnd=hwnd, wndproc=_WNDPROC_INSTANCE, nid=nid,
                  hicon=hicon, hinst=hinst)
    return True


def disable_tray():
    """移除托盘图标并清理消息窗口。"""
    if not _state["hwnd"]:
        return
    try:
        if _state["nid"]:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(_state["nid"]))
        if _state["hicon"]:
            user32.DestroyIcon(_state["hicon"])
        if _state["hwnd"]:
            user32.DestroyWindow(_state["hwnd"])
        if _state["hinst"]:
            user32.UnregisterClassW(_state["class_name"], _state["hinst"])
    finally:
        _state.update(hwnd=None, wndproc=None, nid=None, hicon=None, hinst=None)


# ---------- 运行时生成托盘图标（.ico，蓝色圆角方块 + 白色时钟） ----------

def _dist_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    vv = vx * vx + vy * vy
    t = (wx * vx + wy * vy) / vv if vv else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _render_icon(size: int = 32):
    """2x 超采样绘制 32x32 RGBA 图标。"""
    ss = 2
    big = size * ss
    cx = cy = big / 2.0
    half = 13.5 * ss
    radius = 8 * ss
    ring_r = 11 * ss
    stroke = 1.3 * ss

    def inside_square(x, y):
        dx, dy = abs(x - cx), abs(y - cy)
        if dx <= half - radius or dy <= half - radius:
            return True
        if dx <= half and dy <= half:
            return (dx - (half - radius)) ** 2 + (dy - (half - radius)) ** 2 <= radius ** 2
        return False

    img = [[(0, 0, 0, 0)] * big for _ in range(big)]
    for y in range(big):
        for x in range(big):
            px, py = x + 0.5, y + 0.5
            if inside_square(px, py):
                img[y][x] = (0, 122, 255, 255)
            d = math.hypot(px - cx, py - cy)
            if abs(d - ring_r) <= stroke:
                img[y][x] = (255, 255, 255, 255)
            if (_dist_to_segment(px, py, cx, cy, cx, cy - 6.5 * ss) <= stroke or
                    _dist_to_segment(px, py, cx, cy, cx + 4.5 * ss, cy + 4 * ss) <= stroke):
                img[y][x] = (255, 255, 255, 255)

    out = []
    for y in range(size):
        row = []
        for x in range(size):
            rs = gs = bs = asum = 0
            for dy in range(ss):
                for dx in range(ss):
                    r, g, b, a = img[y * ss + dy][x * ss + dx]
                    rs += r * a
                    gs += g * a
                    bs += b * a
                    asum += a
            if asum:
                row.append((rs // asum, gs // asum, bs // asum, asum // (ss * ss)))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def _build_ico(pixels, size: int = 32) -> bytes:
    xor_size = size * size * 4
    mask_size = ((size + 31) // 32) * 4 * size
    image_size = 40 + xor_size + mask_size
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0, 1, 32, image_size, 22)
    bih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, xor_size + mask_size, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = pixels[y][x]
            xor += bytes((b, g, r, a))
    return header + entry + bih + bytes(xor) + b"\x00" * mask_size


def _logo_pixels(size: int = 32):
    """从 assets/logo.png 读取图标像素矩阵（缺资源/出错返回 None）。"""
    try:
        from PIL import Image
        path = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
        if not path.exists():
            return None
        img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        return [[img.getpixel((x, y))[:4] for x in range(size)] for y in range(size)]
    except Exception:
        return None


def create_icon(path, size: int = 32) -> bool:
    """生成托盘图标文件（优先使用项目 logo，缺失时回退旧手绘图标）。"""
    try:
        pixels = _logo_pixels(size) or _render_icon(size)
        with open(path, "wb") as f:
            f.write(_build_ico(pixels, size))
        return True
    except Exception:
        return False
