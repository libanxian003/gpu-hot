"""Desktop widget: GPU utilization and memory of 92/93/95 via the GPU-Hot hub.

Data: GPU-Hot hub WebSocket (ws://<hub>/socket.io/) pushes
{mode: 'hub', nodes: {name: {status, gpus: {id: {utilization, memory_used, memory_total}}}}}
with memory in MiB. One card per GPU, 3 cards per row, two percentage rings per card.
Left-drag moves the widget; right-click opens the menu.
The window is opaque and DPI-aware to avoid desktop bleed-through and bitmap scaling.
"""
import asyncio
import ctypes
import json
import math
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import websockets
from PIL import Image, ImageDraw, ImageTk

HUB_URL = "ws://100.95.120.29:1312/socket.io/"  # Tencent Cloud hub over Tailscale
NODES = ("92", "93", "95")
COLS = 3
REFRESH_MS = 1000
STALE_S = 10  # no frame for this long -> show disconnected
LOCK_PORT = 47131  # single-instance guard
STATE_FILE = Path(__file__).with_name("state.json")

BG, CARD, FG, MUTED, TRACK = "#1b1d23", "#24272f", "#e6e6e6", "#8a8f98", "#353944"
RED, AMBER, GREEN = "#e5534b", "#d4a72c", "#57ab5a"
FONT = "Segoe UI"
PAD, HEADER = 10, 26
CARD_W, CARD_H, GAP = 176, 128, 8
RING_R, RING_W = 27, 7


def level_color(pct):
    return RED if pct >= 90 else AMBER if pct >= 60 else GREEN


class Feed:
    """Background WebSocket reader keeping the latest hub frame."""

    def __init__(self):
        self.nodes = {}
        self.last = 0.0
        self.error = "Connecting..."
        threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True).start()

    async def _run(self):
        delay = 2
        while True:
            try:
                async with websockets.connect(HUB_URL, proxy=None, open_timeout=10,
                                              max_size=None) as ws:
                    delay = 2
                    async for raw in ws:
                        data = json.loads(raw)
                        if data.get("mode") == "hub":
                            self.nodes = data.get("nodes") or {}
                            self.last = time.time()
                            self.error = ""
            except Exception as e:  # network errors: show and reconnect
                self.error = type(e).__name__
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


class Widget:
    def __init__(self, root):
        self.root = root
        self.feed = Feed()
        self.state = self._load_state()
        root.overrideredirect(True)
        root.attributes("-alpha", 1.0)
        root.attributes("-topmost", self.state.get("topmost", False))
        root.geometry(f"+{self.state.get('x', 40)}+{self.state.get('y', 40)}")
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack()
        self.ring_images = {}
        self.topmost_var = tk.BooleanVar(value=self.state.get("topmost", False))
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_checkbutton(label="Always on top", variable=self.topmost_var,
                                  command=self._toggle_top)
        self.menu.add_command(label="Exit", command=root.destroy)
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._save_state())
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.tick()

    # ---- persistence / interaction ----
    def _load_state(self):
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        self.state.update(x=self.root.winfo_x(), y=self.root.winfo_y(),
                          topmost=self.topmost_var.get())
        STATE_FILE.write_text(json.dumps(self.state), encoding="utf-8")

    def _toggle_top(self):
        self.root.attributes("-topmost", self.topmost_var.get())
        self._save_state()

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---- rendering ----
    def _cards(self, nodes):
        """[(node, gid, gpu_dict | None)] in NODES order; None marks an offline node."""
        cards = []
        for n in NODES:
            d = nodes.get(n) or {}
            gpus = (d.get("gpus") or {}) if d.get("status") == "online" else {}
            if not gpus:
                cards.append((n, None, None))
                continue
            for gid in sorted(gpus, key=lambda g: int(g) if str(g).isdigit() else 0):
                cards.append((n, gid, gpus[gid]))
        return cards

    def _ring(self, cx, cy, pct, caption, sub):
        c = self.canvas
        pct = max(0.0, min(pct, 100.0))
        # Supersample only the ring; keep text native for sharp font rendering.
        scale = 4
        size = 2 * RING_R + RING_W + 4
        image = Image.new("RGB", (size * scale, size * scale), CARD)
        draw = ImageDraw.Draw(image)
        inset = (RING_W / 2 + 2) * scale
        box = (inset, inset, size * scale - inset, size * scale - inset)
        draw.ellipse(box, outline=TRACK, width=RING_W * scale)
        if pct >= 99.95:
            draw.ellipse(box, outline=level_color(pct), width=RING_W * scale)
        elif pct > 0:
            draw.arc(box, -90, -90 + pct * 3.6, fill=level_color(pct), width=RING_W * scale)
        photo = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS))
        self.ring_images[(cx, cy)] = photo
        c.create_image(cx, cy, image=photo)
        c.create_text(cx, cy, text=f"{pct:.0f}%", fill=FG, font=(FONT, 11, "bold"))
        c.create_text(cx, cy + RING_R + 13, text=caption, fill=MUTED, font=(FONT, 8, "bold"))
        if sub:
            c.create_text(cx, cy + RING_R + 26, text=sub, fill=MUTED, font=(FONT, 8))

    def _card(self, x, y, node, gid, g):
        c = self.canvas
        c.create_rectangle(x, y, x + CARD_W, y + CARD_H, fill=CARD, width=0)
        if g is None:
            c.create_text(x + 10, y + 14, text=f"Server {node}", anchor="w", fill=FG,
                          font=(FONT, 9, "bold"))
            c.create_text(x + CARD_W / 2, y + CARD_H / 2, text="OFFLINE", fill=RED,
                          font=(FONT, 12, "bold"))
            return
        model = (g.get("name") or "").replace("NVIDIA GeForce ", "")
        c.create_text(x + 10, y + 14, text=f"{node} · GPU{gid}", anchor="w", fill=FG,
                      font=(FONT, 9, "bold"))
        c.create_text(x + CARD_W - 10, y + 14, text=model, anchor="e", fill=MUTED, font=(FONT, 8))
        util = float(g.get("utilization") or 0)
        used = float(g.get("memory_used") or 0)
        total = float(g.get("memory_total") or 0) or 1.0
        cy = y + 30 + RING_R + 4
        self._ring(x + CARD_W * 0.27, cy, util, "UTIL", "")
        self._ring(x + CARD_W * 0.73, cy, used / total * 100, "VRAM",
                   f"{used / 1024:.1f} / {total / 1024:.0f} GB")

    def tick(self):
        c = self.canvas
        cards = self._cards(self.feed.nodes)
        cols = min(COLS, max(1, len(cards)))
        rows = math.ceil(len(cards) / cols)
        width = PAD * 2 + cols * CARD_W + (cols - 1) * GAP
        height = PAD * 2 + HEADER + rows * CARD_H + (rows - 1) * GAP
        c.configure(width=width, height=height)
        c.delete("all")
        c.create_text(PAD, PAD + 8, text="GPU Cluster", anchor="w", fill=FG,
                      font=(FONT, 10, "bold"))
        if self.feed.last == 0 or time.time() - self.feed.last > STALE_S:
            status, color = self.feed.error or "Disconnected", RED
        else:
            status, color = time.strftime("Updated %H:%M:%S", time.localtime(self.feed.last)), MUTED
        c.create_text(width - PAD, PAD + 8, text=status, anchor="e", fill=color, font=(FONT, 8))
        for i, (node, gid, g) in enumerate(cards):
            r, col = divmod(i, cols)
            self._card(PAD + col * (CARD_W + GAP), PAD + HEADER + r * (CARD_H + GAP), node, gid, g)
        self.root.after(REFRESH_MS, self.tick)


def main():
    # Must precede Tk creation: otherwise Windows bitmap-scales the whole window.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        sys.exit(0)  # already running
    root = tk.Tk()
    root.title("GPU Widget")
    Widget(root)
    root.mainloop()


if __name__ == "__main__":
    main()
