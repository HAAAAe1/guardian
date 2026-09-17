"""
Guardian - 摄像头人物检测 + 窗口自动隐藏
检测到多人时自动隐藏指定窗口，人离开后恢复显示。
支持运行时动态选择要隐藏的窗口。
"""

import json
import os
import sys
import time
import threading
import ctypes
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO
import pystray
from PIL import Image, ImageDraw

# ── Win32 API ──────────────────────────────────────────────
user32 = ctypes.windll.user32

SW_HIDE = 0
SW_SHOW = 5

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
ShowWindow = user32.ShowWindow
GetWindowThreadProcessId = user32.GetWindowThreadProcessId

# ── 配置 ───────────────────────────────────────────────────
APP_NAME = "Guardian"
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "threshold": 2,
    "interval": 0.5,
    "confidence": 0.5,
    "target_windows": [],   # 窗口标题列表（运行时动态选）
    "camera_index": 0,
    "enabled": True,
}


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── 窗口操作 ───────────────────────────────────────────────
def enum_windows():
    """枚举所有顶层可见窗口，返回 [(hwnd, title, pid), ...]"""
    results = []

    def callback(hwnd, _):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                pid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                results.append((hwnd, buf.value, pid.value))
        return True

    EnumWindows(EnumWindowsProc(callback), 0)
    return results


def find_target_windows(cfg):
    """根据配置找到要隐藏的窗口句柄"""
    windows = enum_windows()
    targets = []
    for hwnd, title, pid in windows:
        for pattern in cfg["target_windows"]:
            if pattern == title:  # 精确匹配标题
                targets.append(hwnd)
                break
    return targets


def hide_windows(hwnds):
    for hwnd in hwnds:
        ShowWindow(hwnd, SW_HIDE)


def show_windows(hwnds):
    for hwnd in hwnds:
        ShowWindow(hwnd, SW_SHOW)


# ── 窗口选择器 (tkinter) ───────────────────────────────────
def open_window_picker(cfg, guardian):
    """弹出窗口选择界面，动态选择要隐藏的窗口"""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Guardian - 选择要隐藏的窗口")
    root.geometry("500x500")
    root.attributes("-topmost", True)

    # 当前已选
    selected = set(cfg["target_windows"])

    # 刷新按钮 + 列表
    frame_top = tk.Frame(root)
    frame_top.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(frame_top, text="勾选检测到多人时要自动隐藏的窗口：", font=("微软雅黑", 10)).pack(side=tk.LEFT)

    # Canvas + Scrollbar
    canvas_frame = tk.Frame(root)
    canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    canvas = tk.Canvas(canvas_frame)
    scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # 鼠标滚轮
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    vars_dict = {}

    def refresh_list():
        """刷新窗口列表"""
        for widget in scrollable_frame.winfo_children():
            widget.destroy()

        windows = enum_windows()
        seen = set()
        row = 0
        for hwnd, title, pid in windows:
            if title in seen:
                continue
            seen.add(title)

            var = tk.BooleanVar(value=(title in selected))
            vars_dict[title] = var

            cb = tk.Checkbutton(
                scrollable_frame,
                text=f"[{pid}] {title}",
                variable=var,
                anchor="w",
                font=("Consolas", 9),
            )
            cb.grid(row=row, column=0, sticky="w", padx=5, pady=1)
            row += 1

    refresh_list()

    # 底部按钮
    btn_frame = tk.Frame(root)
    btn_frame.pack(fill=tk.X, padx=10, pady=10)

    def apply_selection():
        """应用选择"""
        new_targets = [title for title, var in vars_dict.items() if var.get()]
        cfg["target_windows"] = new_targets
        save_config(cfg)
        # 更新 Guardian 的配置
        guardian.cfg = cfg
        root.destroy()

    def do_refresh():
        nonlocal vars_dict
        vars_dict = {}
        refresh_list()

    tk.Button(btn_frame, text="🔄 刷新列表", command=do_refresh, font=("微软雅黑", 9)).pack(side=tk.LEFT)
    tk.Button(btn_frame, text="✅ 确认", command=apply_selection, font=("微软雅黑", 10, "bold"),
              bg="#4CAF50", fg="white").pack(side=tk.RIGHT, padx=5)
    tk.Button(btn_frame, text="取消", command=root.destroy, font=("微软雅黑", 9)).pack(side=tk.RIGHT)

    root.mainloop()


# ── 检测核心 ───────────────────────────────────────────────
class Guardian:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.cap = None
        self.running = True
        self.enabled = cfg["enabled"]
        self.is_hidden = False
        self.hidden_hwnds = []
        self.person_count = 0
        self.lock = threading.Lock()
        self.status = "初始化中..."

    def init_model(self):
        self.status = "加载模型..."
        self.model = YOLO("yolov8n.pt")
        self.status = "模型就绪"

    def init_camera(self):
        self.status = "打开摄像头..."
        self.cap = cv2.VideoCapture(self.cfg["camera_index"])
        if not self.cap.isOpened():
            self.status = "❌ 摄像头打开失败"
            return False
        self.status = "就绪"
        return True

    def detect(self):
        if not self.cap or not self.cap.isOpened():
            return 0
        ret, frame = self.cap.read()
        if not ret:
            return 0
        results = self.model(frame, conf=self.cfg["confidence"], classes=[0], verbose=False)
        count = 0
        for r in results:
            if r.boxes is not None:
                count = len(r.boxes)
        return count

    def run(self):
        self.init_model()
        if not self.init_camera():
            return

        interval = self.cfg["interval"]
        while self.running:
            if not self.enabled:
                time.sleep(1)
                continue
            try:
                count = self.detect()
                with self.lock:
                    self.person_count = count

                threshold = self.cfg["threshold"]
                if count >= threshold and not self.is_hidden:
                    hwnds = find_target_windows(self.cfg)
                    if hwnds:
                        hide_windows(hwnds)
                        with self.lock:
                            self.hidden_hwnds = hwnds
                            self.is_hidden = True
                            self.status = f"🔴 已隐藏 ({len(hwnds)} 个窗口)"

                elif count < threshold and self.is_hidden:
                    with self.lock:
                        show_windows(self.hidden_hwnds)
                        self.hidden_hwnds = []
                        self.is_hidden = False
                        self.status = f"🟢 恢复显示 (检测到 {count} 人)"

                elif not self.is_hidden:
                    with self.lock:
                        self.status = f"🟢 监控中 (检测到 {count} 人)"

            except Exception as e:
                with self.lock:
                    self.status = f"⚠️ 错误: {e}"

            time.sleep(interval)

        if self.is_hidden:
            show_windows(self.hidden_hwnds)
        if self.cap:
            self.cap.release()

    def stop(self):
        self.running = False

    def toggle(self):
        self.enabled = not self.enabled
        if not self.enabled and self.is_hidden:
            show_windows(self.hidden_hwnds)
            with self.lock:
                self.hidden_hwnds = []
                self.is_hidden = False

    def manual_hide(self):
        if not self.is_hidden:
            hwnds = find_target_windows(self.cfg)
            if hwnds:
                hide_windows(hwnds)
                with self.lock:
                    self.hidden_hwnds = hwnds
                    self.is_hidden = True
                    self.status = "🔴 手动隐藏"

    def manual_show(self):
        if self.is_hidden:
            show_windows(self.hidden_hwnds)
            with self.lock:
                self.hidden_hwnds = []
                self.is_hidden = False
                self.status = "🟢 手动恢复"


# ── 托盘图标 ───────────────────────────────────────────────
def create_icon(color="green"):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color_map = {"green": (0, 180, 0), "red": (200, 0, 0), "yellow": (200, 180, 0)}
    c = color_map.get(color, (128, 128, 128))
    draw.ellipse([8, 8, 56, 56], fill=(*c, 255))
    draw.arc([16, 12, 48, 52], 0, 360, fill=(255, 255, 255, 200), width=3)
    return img


def get_tray_icon(guardian):
    if guardian.is_hidden:
        return create_icon("red")
    elif guardian.enabled:
        return create_icon("green")
    else:
        return create_icon("yellow")


def run_tray(guardian):
    cfg = guardian.cfg

    def on_toggle(icon, item):
        guardian.toggle()
        icon.icon = get_tray_icon(guardian)

    def on_hide(icon, item):
        guardian.manual_hide()
        icon.icon = get_tray_icon(guardian)

    def on_show(icon, item):
        guardian.manual_show()
        icon.icon = get_tray_icon(guardian)

    def on_pick(icon, item):
        """打开窗口选择器（在新线程里跑 tkinter）"""
        threading.Thread(target=open_window_picker, args=(cfg, guardian), daemon=True).start()

    def on_exit(icon, item):
        guardian.stop()
        icon.stop()

    def on_status(icon, item):
        targets = ", ".join(cfg["target_windows"]) if cfg["target_windows"] else "(未选择)"
        msg = (
            f"状态: {guardian.status}\n"
            f"检测人数: {guardian.person_count}\n"
            f"已隐藏窗口: {len(guardian.hidden_hwnds)}\n"
            f"阈值: {cfg['threshold']}人\n"
            f"监控: {'开启' if guardian.enabled else '关闭'}\n\n"
            f"目标窗口:\n{targets}"
        )
        ctypes.windll.user32.MessageBoxW(0, msg, "Guardian 状态", 0x40)

    menu = pystray.Menu(
        pystray.MenuItem("📊 状态", on_status, default=True),
        pystray.MenuItem("☑️ 选择要隐藏的窗口", on_pick),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔄 开关监控", on_toggle),
        pystray.MenuItem("🔴 手动隐藏", on_hide),
        pystray.MenuItem("🟢 手动恢复", on_show),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌ 退出", on_exit),
    )

    icon = pystray.Icon(APP_NAME, get_tray_icon(guardian), APP_NAME, menu)

    def update_icon():
        while guardian.running:
            icon.icon = get_tray_icon(guardian)
            time.sleep(1)

    threading.Thread(target=update_icon, daemon=True).start()
    icon.run()


# ── 入口 ────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "help":
            print(f"{APP_NAME} - 摄像头人物检测窗口隐藏工具")
            print()
            print("用法:")
            print(f"  python {sys.argv[0]}          启动（托盘图标操作）")
            print(f"  python {sys.argv[0]} help     显示帮助")
            print()
            print("操作:")
            print("  托盘图标右键 → 选择要隐藏的窗口 → 监控自动运行")
            return

    cfg = load_config()
    guardian = Guardian(cfg)
    detect_thread = threading.Thread(target=guardian.run, daemon=True)
    detect_thread.start()

    print("[Guardian] 监控已启动")
    print("[Guardian] 右键托盘图标 → 「选择要隐藏的窗口」动态配置")
    print("[Guardian] 「状态」查看当前信息")

    try:
        run_tray(guardian)
    except KeyboardInterrupt:
        guardian.stop()


if __name__ == "__main__":
    main()
