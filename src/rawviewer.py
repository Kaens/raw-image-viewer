#!/usr/bin/env python3
"""
rawviewer.py

Raw image viewer — viewport rendering, precise page-up/page-down (2/3 visible area),
input focus parking, keyboard shortcuts bound to canvas only.

Dependencies:
 - tkinter (stdlib)
 - Pillow (pip install pillow) for rendering & saving PNG

Run:
    py -3.13 rawviewer.py
    or
    python rawviewer.py
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import math
import os
import sys
import traceback
import importlib.util

# --- bootstrap diagnostic & Pillow import ---
def _bootstrap_info():
    print("=== rawviewer bootstrap ===")
    print("sys.executable:", sys.executable)
    print("sys.version:", sys.version.splitlines()[0])
    print("cwd:", os.getcwd())
    print("=== end bootstrap ===\n")
    sys.stdout.flush()

_bootstrap_info()

try:
    from PIL import Image, ImageTk
except Exception as e:
    Image = None
    ImageTk = None
    print("Pillow import failed:", repr(e))
    traceback.print_exc()
    print(f"Install Pillow into this interpreter:\n    {sys.executable} -m pip install --upgrade Pillow")
    sys.stdout.flush()

# ---------------------------
# Bit reading helpers
# ---------------------------
def read_bits_msb(data: bytearray, bitpos: int, nbits: int) -> int:
    val = 0
    total_bits = len(data) * 8
    for i in range(nbits):
        p = bitpos + i
        if p >= total_bits:
            bit = 0
        else:
            bidx = p // 8
            bit_in_byte = p % 8
            bit = (data[bidx] >> (7 - bit_in_byte)) & 1
        val = (val << 1) | bit
    return val

def read_bits_lsb(data: bytearray, bitpos: int, nbits: int) -> int:
    val = 0
    total_bits = len(data) * 8
    for i in range(nbits):
        p = bitpos + i
        if p >= total_bits:
            bit = 0
        else:
            bidx = p // 8
            bit_in_byte = p % 8
            bit = (data[bidx] >> bit_in_byte) & 1
        val |= (bit << i)
    return val

def adjust_endianness_for_pixel(pixel_val: int, bpp: int, byte_order: str) -> int:
    """Reverse whole-byte order for multi-byte pixels when byte_order == 'le'."""
    if byte_order.lower() not in ('le', 'be') or bpp <= 8:
        mask = (1 << bpp) - 1 if bpp < 64 else -1
        return pixel_val & mask
    nbytes = (bpp + 7) // 8
    bytes_list = []
    for i in range(nbytes):
        shift = (nbytes - 1 - i) * 8
        b = (pixel_val >> shift) & 0xFF
        bytes_list.append(b)
    if byte_order.lower() == 'le':
        bytes_list.reverse()
    val = 0
    for b in bytes_list:
        val = (val << 8) | b
    mask = (1 << bpp) - 1
    return val & mask

# ---------------------------
# Presets
# ---------------------------

PRESETS = [
    {'label': '1-bit: Monochrome (MSB)',       'bits_allowed': [1], 'order': 'msb', 'fields': [('gray',1)]},
    {'label': '1-bit: Monochrome (LSB)',       'bits_allowed': [1], 'order': 'lsb', 'fields': [('gray',1)]},

    {'label': '4-bit: Grayscale (0..15)',      'bits_allowed': [4], 'order': 'msb', 'fields': [('gray',4)]},
    {'label': '4-bit: 2R-1G-1B (toy-pal)',     'bits_allowed': [4], 'order': 'msb', 'fields': [('r',2), ('g',1), ('b',1)]},

    {'label': '8-bit: Grayscale (0..255)',     'bits_allowed': [8], 'order': 'msb', 'fields': [('gray',8)]},
    {'label': '8-bit: R3-G3-B2',               'bits_allowed': [8], 'order': 'msb', 'fields': [('r',3), ('g',3), ('b',2)]},
    {'label': '8-bit: B3-G3-R2',               'bits_allowed': [8], 'order': 'msb', 'fields': [('b',3), ('g',3), ('r',2)]},
    {'label': '8-bit: R2-G3-B3',               'bits_allowed': [8], 'order': 'msb', 'fields': [('r',2), ('g',3), ('b',3)]},
    {'label': '8-bit: A2-R2-G2-B2',            'bits_allowed': [8], 'order': 'msb', 'fields': [('a',2), ('r',2), ('g',2), ('b',2)]},
    {'label': '8-bit: A1-R2-G3-B2',            'bits_allowed': [8], 'order': 'msb', 'fields': [('a',1), ('r',2), ('g',3), ('b',2)]},

    {'label': '16-bit: R5-G6-B5',              'bits_allowed': [16], 'order': 'msb', 'fields': [('r',5), ('g',6), ('b',5)]},
    {'label': '16-bit: A1-R5-G5-B5',           'bits_allowed': [16], 'order': 'msb', 'fields': [('a',1), ('r',5), ('g',5), ('b',5)]},
    {'label': '16-bit: R4-G4-B4-A4',           'bits_allowed': [16], 'order': 'msb', 'fields': [('r',4), ('g',4), ('b',4), ('a',4)]},
    {'label': '16-bit: R3-G4-B3 (10-bit packed)', 'bits_allowed': [16], 'order': 'msb', 'fields': [('r',3), ('g',4), ('b',3)]},
    {'label': '16-bit: B3-G4-R3 (10-bit packed)', 'bits_allowed': [16], 'order': 'msb', 'fields': [('b',3), ('g',4), ('r',3)]},
    {'label': '16-bit: A1-R3-G3-B3 (12-bit+pad)', 'bits_allowed': [16], 'order': 'msb', 'fields': [('a',1), ('r',3), ('g',3), ('b',3)]},
]

PRESETS_BY_BPP = {}
for p in PRESETS:
    for b in p['bits_allowed']:
        PRESETS_BY_BPP.setdefault(b, []).append(p)

# ---------------------------
# App
# ---------------------------

class RawViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Raw Image Viewer")
        self.geometry("1100x700")

        # State
        self.data = bytearray()
        self.filename = None

        self.start_offset_bytes = 0    # integer byte offset
        self.width_px = 256
        self.bpp = 8
        self.bit_align = 0             # 0..7
        self.bit_order = 'msb'         # 'msb' or 'lsb'
        self.byte_order = 'be'         # 'be' or 'le'
        self.preset = PRESETS_BY_BPP.get(self.bpp, [PRESETS[0]])[0]

        # images
        self._tk_image = None
        self._current_image = None

        # build UI
        self._make_ui()
        # bind keys to canvas
        self._bind_canvas_keys()
        self._update_status()

    # ---------------------------
    # UI
    # ---------------------------
    def _make_ui(self):
        mainframe = ttk.Frame(self)
        mainframe.pack(fill='both', expand=True)

        sidebar = ttk.Frame(mainframe, width=300, padding=6)
        sidebar.pack(side='left', fill='y')

        viewframe = ttk.Frame(mainframe)
        viewframe.pack(side='right', fill='both', expand=True)

        ttk.Label(sidebar, text="Raw Viewer", font=('TkDefaultFont', 14, 'bold')).pack(pady=(0,8))

        # Load / Save
        btn_frame = ttk.Frame(sidebar)
        btn_frame.pack(fill='x', pady=(0,8))
        self.load_btn = ttk.Button(btn_frame, text="Load file...", command=self.load_file)
        self.load_btn.pack(side='left', padx=(0,4))
        self.save_btn = ttk.Button(btn_frame, text="Save PNG...", command=self.save_png)
        self.save_btn.pack(side='left')
        self.save_btn.state(['disabled'])

        # Offset entry
        off_frame = ttk.LabelFrame(sidebar, text="Start offset (bytes)")
        off_frame.pack(fill='x', pady=(8,8))
        self.offset_var = tk.StringVar(value="0")
        self.offset_entry = ttk.Entry(off_frame, textvariable=self.offset_var)
        self.offset_entry.pack(fill='x', padx=4, pady=4)
        ttk.Label(off_frame, text="(Prefix with 0x for hex)").pack(anchor='w', padx=4)
        apply_off_btn = ttk.Button(off_frame, text="Apply offset", command=self.apply_offset)
        apply_off_btn.pack(padx=4, pady=4)
        # Bind Enter on offset entry to commit and park focus
        self.offset_entry.bind("<Return>", lambda e: (self.apply_offset(), self._park_focus()))

        # Width
        width_frame = ttk.LabelFrame(sidebar, text="Width (pixels / row)")
        width_frame.pack(fill='x', pady=(0,8))
        self.width_var = tk.IntVar(value=self.width_px)
        self.width_spin = ttk.Spinbox(width_frame, from_=1, to=32768, textvariable=self.width_var, command=self.on_width_spin)
        self.width_spin.pack(fill='x', padx=4, pady=4)
        # Also apply Enter on spin's internal entry widget
        self._bind_spinbox_return(self.width_spin, self.on_width_spin)

        # Bits per pixel
        bpp_frame = ttk.LabelFrame(sidebar, text="Bits-per-pixel")
        bpp_frame.pack(fill='x', pady=(0,8))
        self.bpp_var = tk.IntVar(value=self.bpp)
        self.bpp_combo = ttk.Combobox(bpp_frame, values=[1,4,8,16], textvariable=self.bpp_var, state='readonly')
        self.bpp_combo.pack(fill='x', padx=4, pady=(6,4))
        self.bpp_combo.bind('<<ComboboxSelected>>', lambda ev: (self.on_bpp_change(), self._park_focus()))

        # preset dropdown
        ttk.Label(bpp_frame, text="Mapping preset:").pack(anchor='w', padx=4)
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(bpp_frame, state='readonly', textvariable=self.preset_var)
        self.preset_combo.pack(fill='x', padx=4, pady=(4,6))
        self._refresh_preset_options()
        self.preset_combo.bind('<<ComboboxSelected>>', lambda ev: (self._on_preset_selected(), self._park_focus()))

        # bit alignment & order
        align_frame = ttk.LabelFrame(sidebar, text="Bit alignment & order")
        align_frame.pack(fill='x', pady=(0,8))
        self.bitoffset_var = tk.IntVar(value=self.bit_align)
        self.bitoffset_spin = ttk.Spinbox(align_frame, from_=0, to=7, textvariable=self.bitoffset_var, command=self.on_bitoffset_spin)
        self.bitoffset_spin.pack(fill='x', padx=4, pady=(6,4))
        self._bind_spinbox_return(self.bitoffset_spin, self.on_bitoffset_spin)

        self.bitorder_var = tk.StringVar(value=self.bit_order)
        ttk.Radiobutton(align_frame, text="MSB-first (bits)", variable=self.bitorder_var, value='msb', command=lambda: (self.on_bitorder_change(), self._park_focus())).pack(anchor='w', padx=4)
        ttk.Radiobutton(align_frame, text="LSB-first (bits)", variable=self.bitorder_var, value='lsb', command=lambda: (self.on_bitorder_change(), self._park_focus())).pack(anchor='w', padx=4)

        ttk.Label(align_frame, text="Byte order (for multi-byte pixels):").pack(anchor='w', padx=4, pady=(6,0))
        self.byteorder_var = tk.StringVar(value=self.byte_order)
        ttk.Radiobutton(align_frame, text="Big-endian (BE)", variable=self.byteorder_var, value='be', command=lambda: (self.on_byteorder_change(), self._park_focus())).pack(anchor='w', padx=8)
        ttk.Radiobutton(align_frame, text="Little-endian (LE)", variable=self.byteorder_var, value='le', command=lambda: (self.on_byteorder_change(), self._park_focus())).pack(anchor='w', padx=8)

        # parking focus frame (neutral zone)
        # This tiny frame receives focus after pressing Enter in an entry,
        # so keyboard shortcuts then go to the canvas only.
        self.focus_parking = ttk.Frame(sidebar, height=2, takefocus=True)
        self.focus_parking.pack(fill='x', pady=(8,2))
        # clicking the parking area focuses canvas instead (user convenience)
        self.focus_parking.bind("<Button-1>", lambda e: self.canvas.focus_set())

        tips = ttk.Label(sidebar, text=(
            "Shortcuts (only when image area focused):\n"
            "←/→ change width\n"
            "↑/↓ change offset by 1 byte\n"
            "PgUp/PgDn page +/- (2/3 visible area)\n"
            "Shift+↑/↓ change bpp (1,4,8,16)\n"
            "Shift+←/→ change bit offset\n"
            "Mouse wheel -> small page moves"
        ), justify='left', wraplength=260)
        tips.pack(pady=(8,8))

        # status bar
        self.status_var = tk.StringVar(value="")
        self.status = ttk.Label(self, anchor='w', textvariable=self.status_var, relief='sunken')
        self.status.pack(side='bottom', fill='x')

        # canvas (no scrollbars)
        canvas_outer = ttk.Frame(viewframe)
        canvas_outer.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(canvas_outer, bg='black')
        self.canvas.pack(side='left', fill='both', expand=True)

        # bind configure so we re-render on resize
        self.canvas.bind("<Configure>", lambda e: self.render_image())
        # bind mouse wheel to page move small
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)  # Windows
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)    # Linux up
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)    # Linux down

        # ensure initial focus is on canvas
        self.canvas.focus_set()

    def _bind_spinbox_return(self, spinbox_widget, callback):
        # Spinbox has an internal entry we can bind to
        try:
            entry = spinbox_widget.children.get('entry')  # sometimes the internal name is 'entry'
            if entry:
                entry.bind("<Return>", lambda e: (callback(), self._park_focus()))
            else:
                spinbox_widget.bind("<Return>", lambda e: (callback(), self._park_focus()))
        except Exception:
            spinbox_widget.bind("<Return>", lambda e: (callback(), self._park_focus()))

    def _bind_canvas_keys(self):
        c = self.canvas
        # navigation
        c.bind("<Left>", lambda e: (self._change_width(-1), "break"))
        c.bind("<Right>", lambda e: (self._change_width(+1), "break"))
        c.bind("<Up>", lambda e: (self._move_offset(-1), "break"))
        c.bind("<Down>", lambda e: (self._move_offset(+1), "break"))
        c.bind("<Prior>", lambda e: (self._page_move(-1), "break"))  # PageUp
        c.bind("<Next>",  lambda e: (self._page_move(+1), "break"))  # PageDown

        c.bind("<Shift-Left>", lambda e: (self._change_bit_align(-1), "break"))
        c.bind("<Shift-Right>", lambda e: (self._change_bit_align(+1), "break"))
        c.bind("<Shift-Up>", lambda e: (self._cycle_bpp(+1), "break"))
        c.bind("<Shift-Down>", lambda e: (self._cycle_bpp(-1), "break"))

    # ---------------------------
    # UI callbacks
    # ---------------------------
    def load_file(self):
        fn = filedialog.askopenfilename(title="Open raw file", filetypes=[("All files","*.*")])
        if not fn:
            return
        try:
            with open(fn, 'rb') as f:
                self.data = bytearray(f.read())
            self.filename = fn
            self.save_btn.state(['!disabled'])
            # reset offset
            self.start_offset_bytes = 0
            self.offset_var.set("0")
            self._update_status()
            # park focus onto canvas
            self.canvas.focus_set()
            self.render_image()
        except Exception as e:
            messagebox.showerror("Error", f"Could not read file: {e}")

    def save_png(self):
        if Image is None:
            messagebox.showerror("Missing dependency", "Pillow is required for saving PNG. Install with: pip install pillow")
            return
        if self._current_image is None:
            messagebox.showinfo("Nothing to save", "There is no rendered image to save.")
            return
        fn = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG image","*.png")])
        if not fn:
            return
        try:
            self._current_image.save(fn, "PNG")
            messagebox.showinfo("Saved", f"Saved PNG: {fn}")
        except Exception as e:
            messagebox.showerror("Error saving PNG", str(e))

    def apply_offset(self):
        s = self.offset_var.get().strip()
        try:
            if s.lower().startswith("0x"):
                v = int(s, 16)
            else:
                v = int(s, 10)
            if v < 0:
                raise ValueError("negative")
            self.start_offset_bytes = v
            self._update_status()
            self.render_image()
        except Exception as e:
            messagebox.showerror("Bad offset", f"Could not parse offset: {e}")

    def on_width_spin(self):
        try:
            w = int(self.width_var.get())
            if w < 1:
                w = 1
            self.width_px = w
            self._update_status()
            self.render_image()
        except Exception:
            pass

    def on_bpp_change(self):
        try:
            new_bpp = int(self.bpp_var.get())
            if new_bpp not in (1,4,8,16):
                return
            self.bpp = new_bpp
            self._refresh_preset_options()
            self._update_status()
            self.render_image()
        except Exception:
            pass

    def on_bitoffset_spin(self):
        try:
            b = int(self.bitoffset_var.get())
            if b < 0: b = 0
            if b > 7: b = 7
            self.bit_align = b
            self._update_status()
            self.render_image()
        except Exception:
            pass

    def on_bitorder_change(self):
        self.bit_order = self.bitorder_var.get()
        self._update_status()
        self.render_image()

    def on_byteorder_change(self):
        self.byte_order = self.byteorder_var.get()
        self._update_status()
        self.render_image()

    def _refresh_preset_options(self):
        opts = PRESETS_BY_BPP.get(self.bpp, [])
        if not opts:
            opts = [{'label': f"{self.bpp}-bit: raw->grayscale", 'bits_allowed':[self.bpp], 'order':'msb', 'fields':[('gray', self.bpp)]}]
        labels = [p['label'] for p in opts]
        self.preset_combo['values'] = labels
        if not hasattr(self, 'preset') or self.preset not in opts:
            self.preset = opts[0]
        self.preset_var.set(self.preset['label'])

    def _on_preset_selected(self):
        label = self.preset_var.get()
        opts = PRESETS_BY_BPP.get(self.bpp, [])
        for p in opts:
            if p['label'] == label:
                self.preset = p
                break
        self._update_status()
        self.render_image()

    # ---------------------------
    # Keyboard helpers & focus parking
    # ---------------------------
    def _park_focus(self):
        """Park focus to neutral zone (the hidden frame) so canvas shortcuts work."""
        try:
            # focus parking frame takes focus, then move focus to canvas afterwards
            self.focus_parking.focus_set()
        except Exception:
            pass
        # give a short delay then move focus to canvas (so user sees the commit)
        self.after(20, lambda: self.canvas.focus_set())

    def _change_width(self, delta):
        try:
            self.width_px = max(1, self.width_px + delta)
            self.width_var.set(self.width_px)
            self._update_status()
            self.render_image()
        except Exception:
            pass

    def _move_offset(self, delta_bytes):
        if not self.data:
            return
        self.start_offset_bytes = max(0, self.start_offset_bytes + delta_bytes)
        self.offset_var.set(str(self.start_offset_bytes))
        self._update_status()
        self.render_image()

    def _change_bit_align(self, delta):
        self.bit_align = min(7, max(0, self.bit_align + delta))
        self.bitoffset_var.set(self.bit_align)
        self._update_status()
        self.render_image()

    def _cycle_bpp(self, direction):
        choices = [1,4,8,16]
        i = choices.index(self.bpp)
        i = (i + direction) % len(choices)
        self.bpp = choices[i]
        self.bpp_var.set(self.bpp)
        self._refresh_preset_options()
        self._update_status()
        self.render_image()

    # ---------------------------
    # Page movement (2/3 visible area)
    # ---------------------------
    def _page_move(self, direction):
        """Move by 2/3 of the visible area. direction: -1 PageUp, +1 PageDown."""
        if not self.data:
            return
        # ensure geometry info up-to-date
        self.update_idletasks()
        canvas_h = max(1, self.canvas.winfo_height())    # rows visible
        canvas_w = max(1, self.canvas.winfo_width())
        visible_pixels = self.width_px * canvas_h
        visible_bits = visible_pixels * self.bpp
        page_bits = int(visible_bits * 2 / 3)
        if page_bits <= 0:
            return

        start_bit = self.start_offset_bytes * 8 + self.bit_align
        new_start_bit = start_bit + direction * page_bits
        # clamp
        total_bits = len(self.data) * 8
        new_start_bit = max(0, min(new_start_bit, max(0, total_bits - self.bpp)))
        # update offset and bit align
        self.start_offset_bytes = new_start_bit // 8
        self.bit_align = new_start_bit % 8
        self.offset_var.set(str(self.start_offset_bytes))
        self.bitoffset_var.set(self.bit_align)
        self._update_status()
        self.render_image()

    def _on_mousewheel(self, event):
        # small move: use one row per small wheel, or 3 rows for large delta
        if not self.data:
            return
        # Normalize cross-platform
        delta = 0
        if hasattr(event, 'delta') and event.delta:
            delta = event.delta
        elif hasattr(event, 'num'):
            # Button-4 up, Button-5 down
            delta = 120 if event.num == 4 else -120 if event.num == 5 else 0
        step_rows = 3 if abs(delta) > 120 else 1
        direction = -1 if delta > 0 else +1
        # compute bits to move: step_rows * width_px * bpp
        bits_step = step_rows * self.width_px * self.bpp
        start_bit = self.start_offset_bytes * 8 + self.bit_align
        new_start_bit = start_bit + direction * bits_step
        total_bits = len(self.data) * 8
        new_start_bit = max(0, min(new_start_bit, max(0, total_bits - self.bpp)))
        self.start_offset_bytes = new_start_bit // 8
        self.bit_align = new_start_bit % 8
        self.offset_var.set(str(self.start_offset_bytes))
        self.bitoffset_var.set(self.bit_align)
        self._update_status()
        self.render_image()

    # ---------------------------
    # Viewport rendering
    # ---------------------------
    def render_image(self):
        # if Pillow missing, show text
        if Image is None or ImageTk is None:
            self.canvas.delete("all")
            self._current_image = None
            self._tk_image = None
            self.canvas.create_text(10,10, anchor='nw', text=(
                "Pillow not available.\nInstall Pillow for full rendering:\n"
                f"{sys.executable} -m pip install --upgrade Pillow"
            ), fill='white')
            return

        if not self.data:
            self.canvas.delete("all")
            self._current_image = None
            self._tk_image = None
            return

        total_bits = len(self.data) * 8
        start_bit = self.start_offset_bytes * 8 + self.bit_align
        if start_bit >= total_bits:
            self.canvas.delete("all")
            self._current_image = None
            self._tk_image = None
            self._update_status()
            return

        # Determine visible rows (one canvas pixel row = one image row)
        self.update_idletasks()
        canvas_h = max(1, self.canvas.winfo_height())
        canvas_w = max(1, self.canvas.winfo_width())

        width = max(1, int(self.width_px))
        visible_rows = canvas_h

        # how many pixels we must render: visible_rows * width
        pixels_to_render = visible_rows * width

        # how many pixels are actually available from this start
        pixels_available = max(0, (total_bits - start_bit) // self.bpp)
        pixels_to_render = min(pixels_to_render, pixels_available)

        # cap rows for safety, but visible_rows is small normally
        rows_to_render = math.ceil(pixels_to_render / width) if width > 0 else 0
        rows_to_render = max(1, rows_to_render)

        # guard against enormous allocation
        MAX_SAFE_HEIGHT = 10000  # should be much larger than any real viewport; adjust if needed
        if rows_to_render > MAX_SAFE_HEIGHT:
            rows_to_render = MAX_SAFE_HEIGHT
            pixels_to_render = rows_to_render * width

        # create PIL image for the viewport chunk
        img = Image.new("RGBA", (width, rows_to_render), (0,0,0,255))
        pix = img.load()

        # choose read function
        read_bits = read_bits_msb if self.bit_order == 'msb' else read_bits_lsb
        fields = self.preset['fields']

        bitpos = start_bit
        total_bits = len(self.data) * 8

        # render row by row
        px_idx = 0
        for y in range(rows_to_render):
            for x in range(width):
                if px_idx >= pixels_available:
                    # beyond data, make transparent
                    pix[x,y] = (0,0,0,0)
                else:
                    # read pixel bits
                    pval = read_bits(self.data, bitpos, self.bpp)
                    bitpos += self.bpp
                    pval = adjust_endianness_for_pixel(pval, self.bpp, self.byte_order)
                    # interpret fields (MSB->LSB)
                    remain = self.bpp
                    cur_shift = remain
                    r = g = b = a = 255
                    for comp_name, comp_bits in fields:
                        if comp_bits <= 0:
                            continue
                        use_bits = min(comp_bits, cur_shift)
                        if cur_shift <= 0:
                            comp_raw = 0
                        else:
                            comp_raw = (pval >> (cur_shift - use_bits)) & ((1 << use_bits) - 1)
                        cur_shift -= use_bits
                        comp_val = 0
                        if use_bits > 0:
                            max_raw = (1 << use_bits) - 1
                            comp_val = int((comp_raw * 255) / max_raw) if max_raw > 0 else 0
                        if comp_name == 'r':
                            r = comp_val
                        elif comp_name == 'g':
                            g = comp_val
                        elif comp_name == 'b':
                            b = comp_val
                        elif comp_name == 'a':
                            a = comp_val
                        elif comp_name == 'gray':
                            r = g = b = comp_val
                    pix[x,y] = (r,g,b,a)
                px_idx += 1

        # show the rendered viewport chunk at top-left of canvas
        self._current_image = img
        self._tk_image = ImageTk.PhotoImage(img)
        self.canvas.delete("viewport_image")
        self.canvas.create_image(0, 0, anchor='nw', image=self._tk_image, tags=("viewport_image",))
        # keep canvas logical size at least width x rows_to_render so it's visible
        self.canvas.config(width=canvas_w, height=canvas_h)
        self._update_status(pixels_drawn=px_idx, w=width, h=rows_to_render)

    # ---------------------------
    # Status
    # ---------------------------
    def _update_status(self, pixels_drawn=None, w=None, h=None):
        fname = os.path.basename(self.filename) if self.filename else "(no file)"
        fsize = len(self.data)
        total_bits = len(self.data) * 8
        start_bit = self.start_offset_bytes * 8 + self.bit_align
        if pixels_drawn is None:
            pixels_available = max(0, (total_bits - start_bit) // self.bpp) if self.bpp > 0 else 0
            pixels_drawn = pixels_available
        if w is None or h is None:
            try:
                w = self.width_px
                h = math.ceil(pixels_drawn / max(1, w))
            except Exception:
                h = 0

        status = (f"File: {fname} size={fsize} bytes | "
                  f"offset={self.start_offset_bytes} bit-align={self.bit_align} | "
                  f"width={self.width_px}px bpp={self.bpp} preset='{self.preset['label']}' | "
                  f"bit-order={self.bit_order.upper()} byte-order={self.byte_order.upper()} | "
                  f"pixels_shown={pixels_drawn} view={w}x{h}")
        self.status_var.set(status)

# ---------------------------
# Run
# ---------------------------
def main():
    app = RawViewerApp()
    app.mainloop()

if __name__ == "__main__":
    main()
