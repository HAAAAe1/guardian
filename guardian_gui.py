"""
Guardian GUI - 摄像头人物检测窗口隐藏
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
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw

# 肤色 HSV 范围（用于判断是否有脸）
SKIN_LOWER = np.array([0, 30, 60], dtype=np.uint8)
SKIN_UPPER = np.array([25, 170, 255], dtype=np.uint8)

# ── Win32 ──────────────────────────────────────────────────
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

CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "threshold": 2,
    "interval": 0.5,
    "confidence": 0.5,
    "restore_delay": 3,
    "exclusion_zone": [],
    "face_filter": True,  # 只计有脸的人（过滤后脑勺）
    "target_windows": [],
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
    windows = enum_windows()
    targets = []
    for hwnd, title, pid in windows:
        for pattern in cfg["target_windows"]:
            if pattern == title:
                targets.append(hwnd)
                break
    return targets


SW_MINIMIZE = 6
SW_RESTORE = 9

def hide_windows(hwnds):
    for h in hwnds:
        ShowWindow(h, SW_MINIMIZE)


def show_windows(hwnds):
    for h in hwnds:
        ShowWindow(h, SW_RESTORE)


# ── 检测器 ─────────────────────────────────────────────────
class Detector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.cap = None
        self.running = True
        self.enabled = cfg["enabled"]
        self.is_hidden = False
        self.hidden_hwnds = []
        self.person_count = 0
        self.status = "初始化"
        self.lock = threading.Lock()
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self._below_since = 0  # 低于阈值的起始时间

    def run(self):
        # 模型
        self.status = "加载模型..."
        self.model = YOLO("yolov8n.pt")
        self.status = "打开摄像头..."

        # 摄像头 - DSHOW 后端
        idx = self.cfg["camera_index"]
        self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.status = "❌ 摄像头失败"
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        self.status = "就绪"

        while self.running:
            if not self.enabled:
                time.sleep(0.5)
                continue

            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            # 检测
            small = cv2.resize(frame, (320, 240))
            results = self.model(small, conf=self.cfg["confidence"], classes=[0], verbose=False)

            # 收集所有人框
            all_boxes = []
            for r in results:
                if r.boxes is not None:
                    for box in r.boxes:
                        all_boxes.append(list(map(int, box.xyxy[0].tolist())))

            # 过滤排除区域
            zone = self.cfg.get("exclusion_zone", [])
            if zone and len(zone) == 4:
                zx1, zy1, zx2, zy2 = zone
                filtered = []
                for x1, y1, x2, y2 in all_boxes:
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    if not (zx1 <= cx <= zx2 and zy1 <= cy <= zy2):
                        filtered.append([x1, y1, x2, y2])
                all_boxes = filtered

            # 过滤后脑勺：裁出上半身检测有没有脸（肤色检测）
            if self.cfg.get("face_filter", True):
                with_faces = []
                h, w = small.shape[:2]
                for x1, y1, x2, y2 in all_boxes:
                    # 裁出头部区域（上半部分）
                    crop_y1 = max(0, y1)
                    crop_y2 = min(h, y1 + (y2 - y1) // 2)
                    crop_x1 = max(0, x1)
                    crop_x2 = min(w, x2)
                    if crop_y2 <= crop_y1 or crop_x2 <= crop_x1:
                        continue
                    crop = small[crop_y1:crop_y2, crop_x1:crop_x2]
                    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                    skin_mask = cv2.inRange(hsv, SKIN_LOWER, SKIN_UPPER)
                    skin_ratio = np.count_nonzero(skin_mask) / skin_mask.size
                    # 肤色占比 > 8% 认为有脸
                    if skin_ratio > 0.08:
                        with_faces.append([x1, y1, x2, y2])
                all_boxes = with_faces

            count = len(all_boxes)
            for x1, y1, x2, y2 in all_boxes:
                cv2.rectangle(small, (x1, y1), (x2, y2), (0, 255, 0), 2)

            # 画排除区域（红色虚线框）
            if zone and len(zone) == 4:
                zx1, zy1, zx2, zy2 = zone
                cv2.rectangle(small, (zx1, zy1), (zx2, zy2), (0, 0, 255), 2)
                cv2.putText(small, "EXCLUDE", (zx1, zy1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            with self.frame_lock:
                self.current_frame = rgb
            self.person_count = count

            # 隐藏/恢复逻辑
            threshold = self.cfg["threshold"]
            restore_delay = self.cfg.get("restore_delay", 3)  # 恢复延迟秒数

            if count >= threshold and not self.is_hidden:
                self._below_since = 0
                hwnds = find_target_windows(self.cfg)
                if hwnds:
                    hide_windows(hwnds)
                    self.hidden_hwnds = hwnds
                    self.is_hidden = True
                    self.status = f"🔴 已最小化 {len(hwnds)} 个窗口"
                else:
                    self.status = f"⚠️ 检测到 {count} 人但无匹配窗口"

            elif count < threshold and self.is_hidden:
                now = time.time()
                if self._below_since == 0:
                    self._below_since = now
                    self.status = f"🟢 人数减少，{restore_delay}s 后恢复..."
                elif now - self._below_since >= restore_delay:
                    show_windows(self.hidden_hwnds)
                    self.hidden_hwnds = []
                    self.is_hidden = False
                    self._below_since = 0
                    self.status = f"🟢 已恢复 ({count}人)"
                else:
                    remaining = int(restore_delay - (now - self._below_since))
                    self.status = f"🟢 人数减少，{remaining}s 后恢复..."

            elif not self.is_hidden:
                self._below_since = 0
                self.status = f"🟢 监控中 ({count}人)"

            time.sleep(self.cfg["interval"])

        # 退出恢复
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
            self.hidden_hwnds = []
            self.is_hidden = False


# ── GUI ────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.cfg = load_config()
        self.detector = Detector(self.cfg)

        self.root = tk.Tk()
        self.root.title("Guardian")
        self.root.geometry("440x700")
        self.root.minsize(440, 600)
        self.root.configure(bg="#1e1e1e")

        self._build()
        self._poll()

        # 启动检测线程
        threading.Thread(target=self.detector.run, daemon=True).start()

    def _build(self):
        BG = "#1e1e1e"

        # ── 状态栏 ──
        bar = tk.Frame(self.root, bg="#2a2a2a", padx=10, pady=6)
        bar.pack(fill=tk.X)

        self.dot_c = tk.Canvas(bar, width=14, height=14, bg="#2a2a2a", highlightthickness=0)
        self.dot_c.pack(side=tk.LEFT, padx=(0, 6))
        self.dot = self.dot_c.create_oval(1, 1, 13, 13, fill="#616161", outline="")

        self.lbl_status = tk.Label(bar, text="初始化...", fg="white", bg="#2a2a2a",
                                    font=("微软雅黑", 11, "bold"))
        self.lbl_status.pack(side=tk.LEFT)

        self.lbl_count = tk.Label(bar, text="人数: 0", fg="#aaa", bg="#2a2a2a",
                                   font=("微软雅黑", 9))
        self.lbl_count.pack(side=tk.RIGHT)

        # ── 摄像头 ──
        cam_frame = tk.Frame(self.root, bg=BG)
        cam_frame.pack(fill=tk.X, padx=10, pady=(8, 0))

        self.cam_canvas = tk.Canvas(cam_frame, width=400, height=280, bg="#000",
                                     highlightthickness=1, highlightbackground="#333")
        self.cam_canvas.pack()
        self.cam_canvas.create_text(200, 140, text="等待摄像头...", fill="#555",
                                     font=("微软雅黑", 12), tags="placeholder")

        # 鼠标拖拽画排除区域
        self._drag_start = None
        self._drag_rect = None
        self.cam_canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.cam_canvas.bind("<B1-Motion>", self._on_drag_move)
        self.cam_canvas.bind("<ButtonRelease-1>", self._on_drag_end)

        # 排除区域提示
        hint = tk.Frame(cam_frame, bg=BG)
        hint.pack(fill=tk.X, pady=(2, 0))
        tk.Label(hint, text="💡 在画面上拖拽可画出不检测区域", fg="#888", bg=BG,
                 font=("微软雅黑", 8)).pack(side=tk.LEFT)
        tk.Button(hint, text="清除区域", font=("微软雅黑", 8), bg="#444", fg="white",
                  relief="flat", cursor="hand2", command=self._clear_zone).pack(side=tk.RIGHT)

        # ── 按钮 ──
        btn_bar = tk.Frame(self.root, bg=BG)
        btn_bar.pack(fill=tk.X, padx=10, pady=8)

        bs = {"font": ("微软雅黑", 9), "relief": "flat", "cursor": "hand2", "bd": 0, "padx": 6, "pady": 4}

        self.btn_toggle = tk.Button(btn_bar, text="⏸ 暂停", bg="#444", fg="white",
                                     command=self._toggle, **bs)
        self.btn_toggle.pack(side=tk.LEFT, padx=3)

        # ── 窗口列表 ──
        list_frame = tk.Frame(self.root, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        # 标题行
        hdr = tk.Frame(list_frame, bg=BG)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="☑️ 要隐藏的窗口", fg="white", bg=BG,
                 font=("微软雅黑", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="🔄 刷新", font=("微软雅黑", 8), bg="#444", fg="white",
                  relief="flat", cursor="hand2", command=self._refresh).pack(side=tk.RIGHT)

        # 列表
        lf = tk.Frame(list_frame, bg="#2a2a2a", highlightthickness=1, highlightbackground="#444")
        lf.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        sb = tk.Scrollbar(lf)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.lst = tk.Listbox(lf, font=("Consolas", 9), bg="#2a2a2a", fg="#ddd",
                               selectbackground="#4a6fa5", selectforeground="white",
                               relief="flat", bd=0, activestyle="none",
                               yscrollcommand=sb.set, selectmode=tk.MULTIPLE)
        self.lst.pack(fill=tk.BOTH, expand=True)
        sb.config(command=self.lst.yview)

        self._refresh()

        # ── 底部：阈值 + 保存 ──
        bot = tk.Frame(self.root, bg="#2a2a2a", padx=10, pady=8)
        bot.pack(fill=tk.X, side=tk.BOTTOM)

        tk.Label(bot, text="触发阈值:", fg="#ccc", bg="#2a2a2a",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)

        self.spn_var = tk.IntVar(value=self.cfg["threshold"])
        spn = tk.Spinbox(bot, from_=1, to=10, width=3, textvariable=self.spn_var,
                          font=("微软雅黑", 10), bg="#333", fg="white",
                          buttonbackground="#444", relief="flat",
                          command=self._on_threshold)
        spn.pack(side=tk.LEFT, padx=4)
        tk.Label(bot, text="人", fg="#ccc", bg="#2a2a2a",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)

        tk.Label(bot, text="  恢复延迟:", fg="#ccc", bg="#2a2a2a",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)

        self.delay_var = tk.IntVar(value=self.cfg.get("restore_delay", 3))
        delay_spn = tk.Spinbox(bot, from_=1, to=10, width=3, textvariable=self.delay_var,
                                font=("微软雅黑", 10), bg="#333", fg="white",
                                buttonbackground="#444", relief="flat",
                                command=self._on_delay_change)
        delay_spn.pack(side=tk.LEFT, padx=4)
        tk.Label(bot, text="秒", fg="#ccc", bg="#2a2a2a",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)

        self.face_var = tk.BooleanVar(value=self.cfg.get("face_filter", True))
        face_cb = tk.Checkbutton(bot, text="过滤后脑勺", variable=self.face_var,
                                  fg="#ccc", bg="#2a2a2a", selectcolor="#333",
                                  activebackground="#2a2a2a", activeforeground="#ccc",
                                  font=("微软雅黑", 9), command=self._on_face_toggle)
        face_cb.pack(side=tk.LEFT, padx=(10, 0))

        tk.Button(bot, text="💾 保存选择", font=("微软雅黑", 9, "bold"),
                  bg="#1565c0", fg="white", relief="flat", cursor="hand2",
                  command=self._save).pack(side=tk.RIGHT)

        # ── 关闭 → 托盘 ──
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _refresh(self):
        self.lst.delete(0, tk.END)
        windows = enum_windows()
        seen = set()
        for hwnd, title, pid in windows:
            if title in seen:
                continue
            seen.add(title)
            idx = self.lst.size()
            self.lst.insert(tk.END, f"[{pid}] {title}")
            if title in self.cfg["target_windows"]:
                self.lst.selection_set(idx)

    def _save(self):
        selected = []
        for i in self.lst.curselection():
            text = self.lst.get(i)
            title = text.split("] ", 1)[1] if "] " in text else text
            selected.append(title)
        self.cfg["target_windows"] = selected
        save_config(self.cfg)
        self.detector.cfg = self.cfg
        n = len(selected)
        self.lbl_status.config(text=f"✅ 已保存 {n} 个窗口" if n else "⚠️ 未选择窗口")

    def _on_threshold(self):
        v = self.spn_var.get()
        self.cfg["threshold"] = v
        save_config(self.cfg)
        self.detector.cfg = self.cfg

    def _on_delay_change(self):
        v = self.delay_var.get()
        self.cfg["restore_delay"] = v
        save_config(self.cfg)
        self.detector.cfg = self.cfg

    def _on_face_toggle(self):
        v = self.face_var.get()
        self.cfg["face_filter"] = v
        save_config(self.cfg)
        self.detector.cfg = self.cfg

    def _toggle(self):
        self.detector.toggle()
        self.btn_toggle.config(text="▶️ 启动" if not self.detector.enabled else "⏸ 暂停")

    # ── 排除区域拖拽 ──
    def _on_drag_start(self, event):
        self._drag_start = (event.x, event.y)
        if self._drag_rect:
            self.cam_canvas.delete(self._drag_rect)
        self._drag_rect = self.cam_canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="red", width=2, dash=(4, 4), tags="exclusion"
        )

    def _on_drag_move(self, event):
        if self._drag_start and self._drag_rect:
            x0, y0 = self._drag_start
            self.cam_canvas.coords(self._drag_rect, x0, y0, event.x, event.y)

    def _on_drag_end(self, event):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._drag_start = None

        # 转换到320x240坐标
        zx0 = int(x0 * 320 / 400)
        zy0 = int(y0 * 280 / 280)
        zx1 = int(x1 * 320 / 400)
        zy1 = int(y1 * 280 / 280)

        # 确保左上右下
        zone = [min(zx0, zx1), min(zy0, zy1), max(zx0, zx1), max(zy0, zy1)]

        # 太小的忽略
        if zone[2] - zone[0] < 10 or zone[3] - zone[1] < 10:
            if self._drag_rect:
                self.cam_canvas.delete(self._drag_rect)
                self._drag_rect = None
            return

        self.cfg["exclusion_zone"] = zone
        save_config(self.cfg)
        self.detector.cfg = self.cfg
        self.lbl_status.config(text=f"✅ 排除区域已设置")

    def _clear_zone(self):
        self.cfg["exclusion_zone"] = []
        save_config(self.cfg)
        self.detector.cfg = self.cfg
        self.cam_canvas.delete("exclusion")
        self._drag_rect = None
        self.lbl_status.config(text="✅ 排除区域已清除")

    def _poll(self):
        """每 200ms 刷新界面"""
        st = self.detector.status
        if "🔴" in st:
            c = "#ff1744"
        elif "🟢" in st:
            c = "#00c853"
        elif "⚠" in st:
            c = "#ffd600"
        else:
            c = "#616161"

        self.dot_c.itemconfig(self.dot, fill=c)
        self.lbl_status.config(text=st)
        self.lbl_count.config(text=f"人数: {self.detector.person_count}")

        # 摄像头画面
        with self.detector.frame_lock:
            frame = self.detector.current_frame
        if frame is not None:
            self.cam_canvas.delete("placeholder")
            img = Image.fromarray(frame)
            img = img.resize((400, 280), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self.cam_canvas.create_image(0, 0, anchor=tk.NW, image=self._photo, tags="cam")
            # 保持排除区域框在最上层
            self.cam_canvas.tag_raise("exclusion")

        self.root.after(200, self._poll)

    def _hide_to_tray(self):
        """关闭 → 托盘"""
        self.root.withdraw()
        self._start_tray()

    def _start_tray(self):
        import pystray

        def mk_icon():
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            clr = (200, 0, 0) if self.detector.is_hidden else (0, 180, 0) if self.detector.enabled else (180, 180, 0)
            d.ellipse([8, 8, 56, 56], fill=(*clr, 255))
            d.arc([16, 12, 48, 52], 0, 360, fill=(255, 255, 255, 200), width=3)
            return img

        def on_show(icon, item):
            icon.stop()
            self.root.after(0, self.root.deiconify)

        def on_quit(icon, item):
            self.detector.stop()
            icon.stop()
            self.root.after(0, self.root.destroy)

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", on_show, default=True),
            pystray.MenuItem("退出", on_quit),
        )
        self.tray = pystray.Icon("Guardian", mk_icon(), "Guardian", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def run(self):
        self.root.mainloop()


def main():
    # 隐藏控制台
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), SW_HIDE)
    App().run()


if __name__ == "__main__":
    main()
