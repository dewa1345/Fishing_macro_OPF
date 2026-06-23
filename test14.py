import time
import threading
import tkinter as tk
import PySimpleGUI as sg
import pyautogui
import keyboard
import random  
import mss
from mousekey import MouseKey
import ctypes

# avoid scaling issues
ctypes.windll.shcore.SetProcessDpiAwareness(1)

mkey = MouseKey()

# noted for me graphics 4 and low graphics turn on so the 
# ── CONFIG ────────────────────────────────────────────────────────────────────
# Day + night green variants — for lighting changes
PARTICLE_HEXES  = ["00B358", "009944","03ECAF"]
MINIGAME_HEX    = "E6E6E6"
POLL_INTERVAL   = 0.1
CAST_DELAY      = 2.0
COLOR_TOLERANCE = 35

pyautogui.FAILSAFE = False

macro_running = False
main_window   = None
sct_instance  = None

# ── OVERLAY ───────────────────────────────────────────────────────────────────
class Overlay:
    def __init__(self, label, color, default_x, default_y, size=60):
        self.root = tk.Tk()
        self.root.geometry(f"{size}x{size}+{default_x}+{default_y}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.35)
        self.root.configure(bg=f"#{color}")
        tk.Label(self.root, text=label, bg=f"#{color}", fg="white",
                 font=("Consolas", 7, "bold")).place(x=2, y=2)
        self.resize_margin = 8
        self.resize_mode   = None
        self._drag_start   = (0, 0)
        self.root.bind("<Motion>",         self._update_cursor)
        self.root.bind("<ButtonPress-1>",  self._mouse_down)
        self.root.bind("<B1-Motion>",      self._mouse_move)
        self.root.bind("<ButtonRelease-1>",self._mouse_up)

    def _get_resize_mode(self, x, y):
        w, h, m = self.root.winfo_width(), self.root.winfo_height(), self.resize_margin
        left, right, top, bottom = x<=m, x>=w-m, y<=m, y>=h-m
        if top and left:   return "nw"
        if top and right:  return "ne"
        if bottom and left:return "sw"
        if bottom and right:return "se"
        if left:  return "w"
        if right: return "e"
        if top:   return "n"
        if bottom:return "s"
        return None

    def _update_cursor(self, e):
        cursors = {"n":"sb_v_double_arrow","s":"sb_v_double_arrow",
                   "e":"sb_h_double_arrow","w":"sb_h_double_arrow",
                   "nw":"size_nw_se","se":"size_nw_se",
                   "ne":"size_ne_sw","sw":"size_ne_sw"}
        self.root.config(cursor=cursors.get(self._get_resize_mode(e.x, e.y), "arrow"))

    def _mouse_down(self, e):
        self.resize_mode   = self._get_resize_mode(e.x, e.y)
        self.start_x       = e.x_root
        self.start_y       = e.y_root
        self.start_w       = self.root.winfo_width()
        self.start_h       = self.root.winfo_height()
        self.start_win_x   = self.root.winfo_x()
        self.start_win_y   = self.root.winfo_y()
        if self.resize_mode is None:
            self._drag_start = (e.x_root - self.root.winfo_x(),
                                e.y_root - self.root.winfo_y())

    def _mouse_move(self, e):
        if self.resize_mode is None:
            self.root.geometry(f"+{e.x_root-self._drag_start[0]}+{e.y_root-self._drag_start[1]}")
            return
        dx, dy = e.x_root-self.start_x, e.y_root-self.start_y
        x, y, w, h = self.start_win_x, self.start_win_y, self.start_w, self.start_h
        if "e" in self.resize_mode: w += dx
        if "s" in self.resize_mode: h += dy
        if "w" in self.resize_mode: x += dx; w -= dx
        if "n" in self.resize_mode: y += dy; h -= dy
        self.root.geometry(f"{max(30,w)}x{max(30,h)}+{x}+{y}")

    def _mouse_up(self, e): self.resize_mode = None

    def get_region(self):
        self.root.update_idletasks()
        return (self.root.winfo_x(), self.root.winfo_y(),
                self.root.winfo_width(), self.root.winfo_height())

    def destroy(self): self.root.destroy()

# ── GREEN DETECTION (multi-hex for day/night) ─────────────────────────────────
def find_color_in_region(hex_list, region, tolerance=COLOR_TOLERANCE):
    """Accepts a single hex string or list of hex strings."""
    if isinstance(hex_list, str):
        hex_list = [hex_list]
    targets = [(int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)) for h in hex_list]

    rx, ry, rw, rh = region
    shot = pyautogui.screenshot(region=(rx, ry, rw, rh))
    found_x, found_y = [], []

    for x in range(0, shot.width, 3):
        for y in range(0, shot.height, 3):
            pr, pg, pb = shot.getpixel((x, y))
            for r, g, b in targets:
                if abs(pr-r)<=tolerance and abs(pg-g)<=tolerance and abs(pb-b)<=tolerance:
                    found_x.append(x); found_y.append(y)
                    break

    if found_x:
        return (rx + sum(found_x)//len(found_x),
                ry + sum(found_y)//len(found_y))
    return None

def wait_for_color_in_region(hex_list, region, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not macro_running: return None
        pos = find_color_in_region(hex_list, region)
        if pos: return pos
        time.sleep(POLL_INTERVAL)
    return None

# ── WHITE DETECTION ───────────────────────────────────────────────────────────
def find_white_in_region(hex_color, region, tolerance=50):
    from collections import defaultdict
    rx, ry, rw, rh = region
    r_t = int(hex_color[0:2],16)
    g_t = int(hex_color[2:4],16)
    b_t = int(hex_color[4:6],16)
    CELL = 20
    try:
        sct_img = sct_instance.grab({"top":ry,"left":rx,"width":rw,"height":rh})
        pixels  = sct_img.bgra
        width, height, total = sct_img.width, sct_img.height, len(pixels)
        cell_counts = defaultdict(int)
        for y in range(height):
            y_off = y * width * 4
            for x in range(width):
                idx = y_off + x*4
                if idx+2 >= total: break
                pb,pg,pr = pixels[idx],pixels[idx+1],pixels[idx+2]
                if abs(pr-r_t)<=tolerance and abs(pg-g_t)<=tolerance and abs(pb-b_t)<=tolerance:
                    cell_counts[(x//CELL, y//CELL)] += 1
        if not cell_counts: return None
        best = max(cell_counts, key=lambda c: cell_counts[c])
        return (rx + best[0]*CELL + CELL//2,
                ry + best[1]*CELL + CELL//2)
    except Exception:
        return None

def wait_for_white_in_region(hex_color, region, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not macro_running: return None
        pos = find_white_in_region(hex_color, region)
        if pos: return pos
        time.sleep(0.01)
    return None

# ── CLICK — mousekey with pyautogui fallback ──────────────────────────────────
def click_at(x, y):
    jx = random.randint(-2, 2)
    jy = random.randint(-2, 2)
    tx, ty = x + jx, y + jy
    try:
        cx, cy = pyautogui.position()
        # mousekey crashes when start == end or distance is 0 — guard it
        if abs(tx - cx) < 3 and abs(ty - cy) < 3:
            raise ValueError("too close")
        mkey.left_click_xy_natural(
            tx, ty,
            delay=0.03,
            min_variation=-2, max_variation=2,
            use_every=8,
            sleeptime=(0.008, 0.012),
            percent=85,
        )
    except Exception:
        # fallback: plain pyautogui click — always works
        pyautogui.moveTo(tx, ty, duration=0.08)
        pyautogui.click()

def safe_move_away(x, y):
    """Move mouse away from button so it doesn't block next scan."""
    try:
        pyautogui.moveTo(x + 15, y - 60, duration=0.03)
    except Exception:
        pass

def log(msg):
    if main_window:
        main_window.write_event_value("-LOG-", f"[{time.strftime('%H:%M:%S')}] {msg}")

# ── MACRO LOOP ────────────────────────────────────────────────────────────────
def fishing_loop(bx, by, green_region, white_region):
    global macro_running, sct_instance
    sct_instance = mss.mss()

    log("Starting in 3 seconds... SWITCH TO ROBLOX!")
    time.sleep(3.0)
    if not macro_running: return

    try:
        round_num      = 0
        timeout_streak = 0  # just incase green detection fails repeatedly caused by desync 
        while macro_running:
            round_num += 1
            log(f"Round {round_num} — casting at ({bx},{by})...")
            click_at(bx, by)
            time.sleep(CAST_DELAY)

            # ── GREEN ──
            log("Watching green zone (30s max)...")
            pos = wait_for_color_in_region(PARTICLE_HEXES, green_region, timeout=30)
            if not macro_running: break

            if pos is None:
                timeout_streak += 1
                log(f"Green timed out ({timeout_streak} in a row)...")
                if timeout_streak >= 3:
                    log("3 timeouts — clicking to reel in first, then recasting...")
                    click_at(bx, by)   # reel in whatever is on the rod
                    time.sleep(1.0)    # wait for reel endlag
                    timeout_streak = 0
                click_at(bx, by)       # cast
                time.sleep(CAST_DELAY)
                continue

            timeout_streak = 0  # green found = reset streak

            log(f"Green detected! Clicking at {pos}")
            click_at(*pos)
            time.sleep(0.5)

            # ── WHITE ──
            log("Watching white zone (2s max)...")
            btn = wait_for_white_in_region(MINIGAME_HEX, white_region, timeout=2)
            if not macro_running: break

            if btn is None:
                log("White not found — recasting...")
                continue

            # ── MINIGAME CLICK LOOP ──
            log("Minigame! Clicking buttons...")
            while macro_running:
                btn = find_white_in_region(MINIGAME_HEX, white_region)
                if btn is None:
                    log("Minigame done!")
                    break
                click_at(*btn)
                safe_move_away(*btn)
                time.sleep(0.06)

    except Exception as e:
        log(f"CRASHED: {e}")
    finally:
        log("Stopped.")
        main_window.write_event_value("-STOPPED-", None)

# ── MAIN UI ───────────────────────────────────────────────────────────────────
def main():
    global macro_running, main_window

    overlay_green = Overlay("GREEN", "007A3D", 600, 400, size=70)
    overlay_white = Overlay("WHITE", "999999", 700, 400, size=70)
    overlay_green.root.withdraw()
    overlay_white.root.withdraw()

    sg.theme("DarkGrey14")
    layout = [
        [sg.Text("🎣 Fishing Macro OPF", font=("Consolas",13,"bold"), text_color="#00B358")],
        [sg.HorizontalSeparator()],
        [sg.Text("Status:"), sg.Text("INACTIVE", key="-STATUS-", text_color="red",
                                     font=("Consolas",10,"bold"))],
        [sg.HorizontalSeparator()],
        [sg.Text("Bait X:"), sg.Input("960", key="-BX-", size=6),
         sg.Text("Y:"),      sg.Input("700", key="-BY-", size=6)],
        [sg.HorizontalSeparator()],
        [sg.Multiline("Position the 2 overlay boxes,\nthen press START.\n",
                      key="-LOG-", size=(38,8), disabled=True,
                      font=("Consolas",8), autoscroll=True)],
        [sg.Button("START  [F5]", key="-TOGGLE-",
                   button_color=("#0E0E14","#00B358"), expand_x=True)],
        [sg.Text("⭐ Star the repo if it helped", font=("Consolas",11), text_color="#04FF00")],
        [sg.Text("Made by dewa1345", font=("Consolas",9), text_color="#04FF00")],
        [sg.Text("version 1.2 (Improved speed)", font=("Consolas",9), text_color="#04FF00")],
    ]
    main_window = sg.Window("Fishing Macro OPF", layout, keep_on_top=True,
                            finalize=True, font=("Consolas",9))
    overlay_green.root.deiconify()
    overlay_white.root.deiconify()

    def toggle():
        global macro_running
        if macro_running:
            macro_running = False
            overlay_green.root.deiconify()
            overlay_white.root.deiconify()
        else:
            macro_running   = True
            green_region    = overlay_green.get_region()
            white_region    = overlay_white.get_region()
            overlay_green.root.withdraw()
            overlay_white.root.withdraw()
            bx = int(main_window["-BX-"].get())
            by = int(main_window["-BY-"].get())
            threading.Thread(target=fishing_loop,
                             args=(bx, by, green_region, white_region),
                             daemon=True).start()
            main_window["-STATUS-"].update("ACTIVE", text_color="#00B358")
            main_window["-TOGGLE-"].update("STOP  [F5]",
                                           button_color=("#0E0E14","#E05555"))

    keyboard.add_hotkey("f5", lambda: main_window.write_event_value("-TOGGLE-", None))

    while True:
        event, values = main_window.read()
        if event == sg.WIN_CLOSED:
            macro_running = False; break
        elif event == "-TOGGLE-": toggle()
        elif event == "-LOG-":    main_window["-LOG-"].print(values["-LOG-"])
        elif event == "-STOPPED-":
            main_window["-STATUS-"].update("INACTIVE", text_color="red")
            main_window["-TOGGLE-"].update("START  [F5]",
                                           button_color=("#0E0E14","#00B358"))
            overlay_green.root.deiconify()
            overlay_white.root.deiconify()

    overlay_green.destroy()
    overlay_white.destroy()
    main_window.close()

if __name__ == "__main__":
    main()