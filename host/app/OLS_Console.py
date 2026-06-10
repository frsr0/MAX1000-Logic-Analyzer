#!/usr/bin/env python3
"""
OLS MaxScope — Protocol Analyzer & Generator for MAX1000
A self-contained GUI for signal capture, protocol decode, and generation.
Supports CLI mode for automated testing.
"""
import sys, os, json, struct, time, threading, math, argparse, itertools, re
from collections import namedtuple
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

__version__ = "1.0.0"

# SPI device backend (30 MHz fast mode)
try:
    from driver.ols_spi_device import (
        OLSDeviceSPI, find_spi_device,
        ANALOG_MODE_DIGITAL8, ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2,
        ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2,
        ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4, ANALOG_MODE_MIXED_DUAL,
        ANALOG_ENABLE_BIT,
        decode_analog_frames, analog_frame_stride,
    )
    HAS_SPI = True
except ImportError:
    HAS_SPI = False
    ANALOG_MODE_DIGITAL8 = 0
    ANALOG_MODE_MIXED1 = 1
    ANALOG_MODE_MIXED2 = 2
    ANALOG_MODE_ANALOG1 = 3
    ANALOG_MODE_ANALOG2 = 4
    ANALOG_MODE_ANALOG4 = 5
    ANALOG_MODE_MIXED2_4 = 6
    ANALOG_MODE_MIXED_DUAL = 7
    ANALOG_ENABLE_BIT = 0x08

try:
    import serial, serial.tools.list_ports
except ImportError:
    print("Install pyserial: pip install pyserial")
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    HAS_TK = True
except:
    HAS_TK = False

# SPI backend only — all constants from driver.spi_protocol

NUM_CHANNELS = 16

# Re-export decoder functions from gui_decoders
from app.gui_decoders import (
    samples_to_channels, modbus_crc16, glitch_filter,
    decode_uart, decode_i2c, decode_spi, decode_modbus,
    DecodedByte, DecodedModbusFrame,
)

# Re-export waveform display from gui_waveform
from app.gui_waveform import WaveformDisplay

# ─── Main Application ───────────────────────────────────────────

class OLScope:
    """Main application: combines device control, waveform view, and protocol tools."""

    def __init__(self, backend='UART', root=None):
        self.dev = None
        self._backend = backend
        self.win = root  # may be None for CLI
        self.ch_data = []
        self.ch_names = [f"CH{i}" for i in range(NUM_CHANNELS)]
        self.capture_mode = ANALOG_ENABLE_BIT  # 16 Dig + 8 Ana
        self.analog_ch0_sel = 0
        self.analog_ch1_sel = 1
        self.analog_ch2_sel = 2
        self.analog_ch3_sel = 3
        self.analog_ch4_sel = 4
        self.analog_ch5_sel = 5
        self.analog_ch6_sel = 6
        self.analog_ch7_sel = 7
        self.last_analog_frames = []
        self.samplerate = 1_000_000
        self.captured_bytes = b''
        # Filter + decoder config (populated by GUI, consumed by _process_decoders)
        self.filter_threshold = 3
        self.filter_enabled = [False] * 8          # per-channel filter toggle
        self.decoder_slots = []                    # list of dicts, up to 8
        self.capture_running = False
        self.capture_progress = (0, 0)
        self.capture_result = None
        self.capture_partial = None
        self.capture_nsamp = 0
        self.capture_stride = 4
        self.capture_window = 50000
        self.capture_type = 'rolling'              # 'single' or 'rolling'
        self._last_live_redraw = 0
        self.stop_evt = threading.Event()
        self._pending_restart = False

        self.logger_running = False
        self.logger_count = 0
        self.logger_stop_evt = threading.Event()
        self.logger_csv_path = ''
        self.logger_rate_hz = 1_000_000
        self.logger_nsamp = 1024

        if not HAS_TK:
            return  # CLI mode only
        self._build_ui()

    def _build_ui(self):
        title = "OLS MaxScope — " + ("SPI @ 30 MHz" if self._backend == 'SPI' else "UART")
        self.win.title(title)
        self.win.geometry("1100x700")
        self.win.minsize(800, 500)

        # ── Toolbar ──
        tb = ttk.Frame(self.win, padding=3)
        tb.pack(fill='x')

        # Port connection group — hidden when connected
        self.port_frame = ttk.Frame(tb)
        self.port_frame.pack(side='left')
        ttk.Label(self.port_frame, text="Port:").pack(side='left')
        self.port_cb = ttk.Combobox(self.port_frame, width=14, state='readonly')
        self.port_cb.pack(side='left', padx=2)
        ttk.Button(self.port_frame, text="Scan", command=self._scan_ports, width=6).pack(side='left', padx=2)
        ttk.Button(self.port_frame, text="Connect", command=self._connect, width=8).pack(side='left', padx=2)
        ttk.Button(self.port_frame, text="Disconnect", command=self._disconnect, width=9).pack(side='left', padx=2)
        self.port_sep = ttk.Separator(tb, orient='vertical')
        self.port_sep.pack(side='left', fill='y', padx=5)

        ttk.Label(tb, text="Rate:").pack(side='left')
        self.rate_cb = ttk.Combobox(tb,
            values=['1kHz','10kHz','100kHz','500kHz','1MHz','2MHz','3MHz','4MHz','6MHz','8MHz','12MHz','16MHz','24MHz','32MHz','48MHz','96MHz','Fast 120MHz'],
            width=10, state='normal')
        self.rate_cb.set('1MHz'); self.rate_cb.pack(side='left', padx=2)
        self.rate_cb.bind('<<ComboboxSelected>>', self._on_rate_changed)
        self.rate_cb.bind('<KeyRelease>', self._on_rate_changed)
        self.rate_cb.bind('<Return>', self._on_rate_changed)

        ttk.Label(tb, text="Time:").pack(side='left')
        self.time_var = tk.StringVar(value='5.000 ms')
        self.time_entry = ttk.Entry(tb, textvariable=self.time_var, width=10)
        self.time_entry.pack(side='left', padx=2)

        ttk.Label(tb, text="Buf:").pack(side='left')
        self.rolling_buf_var = tk.StringVar(value='100 ms')
        self.rolling_buf = ttk.Combobox(tb, textvariable=self.rolling_buf_var,
                                        values=[], width=14, state='normal')
        self.rolling_buf.pack(side='left', padx=2)
        self.rolling_buf.bind('<<ComboboxSelected>>', self._on_rolling_buf_change)
        self.rolling_buf.bind('<KeyRelease>', self._on_rolling_buf_change)
        self.rolling_buf.bind('<Return>', self._on_rolling_buf_change)
        self.buf_estimate_lbl = ttk.Label(tb, text="", font=('Consolas', 7))
        self.buf_estimate_lbl.pack(side='left')

        ttk.Button(tb, text="Capture", command=self._capture, width=8).pack(side='left', padx=2)
        self.stop_btn = ttk.Button(tb, text="Stop", command=self._stop_capture, width=6, state='disabled')
        self.stop_btn.pack(side='left', padx=2)

        # Bind rate/samples changes to update time display
        self.rate_cb.bind('<<ComboboxSelected>>', self._update_time_display)
        self.time_entry.bind('<Return>', self._time_changed)

        # ── Main area — waveform + side panel ──
        main = ttk.Frame(self.win)
        main.pack(fill='both', expand=True, padx=3)

        # Waveform frame (takes remaining space)
        wf_frame = ttk.Frame(main)
        wf_frame.pack(side='left', fill='both', expand=True)
        self.wave = WaveformDisplay(wf_frame, app=self)
        self.wave.pack(fill='both', expand=True)

        # Scrollbar for waveform
        self.scroll = ttk.Scrollbar(wf_frame, orient='horizontal', command=self._on_scroll)
        self.scroll.pack(fill='x')
        self.wave.configure(xscrollcommand=self._update_scroll)

        # Zoom controls
        zf = ttk.Frame(wf_frame)
        zf.pack(fill='x')
        ttk.Button(zf, text="Zoom -", command=lambda: self.wave.set_scale(self.wave.px_scale * 0.5),
                  width=8).pack(side='left', padx=2)
        ttk.Button(zf, text="Zoom +", command=lambda: self.wave.set_scale(self.wave.px_scale * 2),
                  width=8).pack(side='left', padx=2)
        ttk.Button(zf, text="Fit", command=self._fit_view, width=6).pack(side='left', padx=2)

        # Side panel (fixed 320px)
        side = ttk.Frame(main, width=320)
        side.pack(side='right', fill='y')
        side.pack_propagate(False)
        self._build_side_panel(side)

        # ── Status bar ──
        self.status = ttk.Label(self.win, text="Disconnected", relief='sunken', anchor='w')
        self.status.pack(fill='x')

        self._scan_ports()
        self._update_buf_presets()

    def _build_side_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill='both', expand=True)
        self.nb = nb

        # Generator tab
        gen_f = ttk.Frame(nb, padding=5)
        nb.add(gen_f, text="Generator")
        ttk.Label(gen_f, text="Protocol:").grid(row=0, column=0, sticky='w')
        self.gen_proto = ttk.Combobox(gen_f, values=['UART', 'I2C', 'Modbus'], state='readonly', width=10)
        self.gen_proto.set('UART'); self.gen_proto.grid(row=0, column=1, sticky='w')
        self.gen_proto.bind('<<ComboboxSelected>>', self._gen_show_proto_fields)
        ttk.Label(gen_f, text="Baud / Speed:").grid(row=1, column=0, sticky='w')
        self.gen_baud = ttk.Entry(gen_f, width=12)
        self.gen_baud.insert(0, '115200'); self.gen_baud.grid(row=1, column=1, sticky='w')
        self.gen_addr_lbl = ttk.Label(gen_f, text="Slave addr:")
        self.gen_addr_lbl.grid(row=2, column=0, sticky='w')
        self.gen_addr = ttk.Entry(gen_f, width=6)
        self.gen_addr.insert(0, '0x01'); self.gen_addr.grid(row=2, column=1, sticky='w')
        self.gen_func_lbl = ttk.Label(gen_f, text="Func code:")
        self.gen_func_lbl.grid(row=3, column=0, sticky='w')
        self.gen_func = ttk.Entry(gen_f, width=6)
        self.gen_func.insert(0, '0x03'); self.gen_func.grid(row=3, column=1, sticky='w')
        self.gen_tx_lbl = ttk.Label(gen_f, text="TX Pin (SDA):")
        self.gen_tx_lbl.grid(row=4, column=0, sticky='w')
        self.gen_tx_pin = ttk.Combobox(gen_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.gen_tx_pin.set('3'); self.gen_tx_pin.grid(row=4, column=1, sticky='w')
        self.gen_scl_lbl = ttk.Label(gen_f, text="SCL Pin:")
        self.gen_scl_lbl.grid(row=5, column=0, sticky='w')
        self.gen_scl_pin = ttk.Combobox(gen_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.gen_scl_pin.set('1'); self.gen_scl_pin.grid(row=5, column=1, sticky='w')
        ttk.Label(gen_f, text="Data:").grid(row=6, column=0, sticky='nw')
        self.gen_data = tk.Text(gen_f, height=4, width=25)
        self.gen_data.insert('1.0', 'Hello!\nLine2')
        self.gen_data.grid(row=6, column=1)
        self.gen_send_btn = ttk.Button(gen_f, text="Send", command=self._gen_send)
        self.gen_send_btn.grid(row=7, column=0, pady=5)
        self.gen_send_cap_btn = ttk.Button(gen_f, text="Send+Capture", command=self._gen_send_capture)
        self.gen_send_cap_btn.grid(row=7, column=1, pady=5)
        self._gen_show_proto_fields()

        # Accelerometer tab
        acc_f = ttk.Frame(nb, padding=5)
        nb.add(acc_f, text="Accelerometer")
        r = 0
        ttk.Label(acc_f, text="I2C Addr:").grid(row=r, column=0, sticky='w')
        self.acc_addr = ttk.Combobox(acc_f, values=['0x18', '0x19'], state='readonly', width=6)
        self.acc_addr.set('0x18'); self.acc_addr.grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(acc_f, text="I2C Speed:").grid(row=r, column=0, sticky='w')
        self.acc_speed = ttk.Entry(acc_f, width=10)
        self.acc_speed.insert(0, '100000'); self.acc_speed.grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(acc_f, text="SDA Pin:").grid(row=r, column=0, sticky='w')
        self.acc_sda_pin = ttk.Combobox(acc_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.acc_sda_pin.set('2'); self.acc_sda_pin.grid(row=r, column=1, sticky='w')
        ttk.Label(acc_f, text="SCL Pin:").grid(row=r, column=2, padx=4)
        self.acc_scl_pin = ttk.Combobox(acc_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.acc_scl_pin.set('1'); self.acc_scl_pin.grid(row=r, column=3, sticky='w')
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Common Commands:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        bf = ttk.Frame(acc_f)
        bf.grid(row=r, column=0, columnspan=4, pady=2)
        ttk.Button(bf, text="Who Am I", width=10, command=lambda: self._accel_read(0x0F, 1)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read X", width=8, command=lambda: self._accel_read(0x28, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read Y", width=8, command=lambda: self._accel_read(0x2A, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read Z", width=8, command=lambda: self._accel_read(0x2C, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read All", width=8, command=lambda: self._accel_read(0x28, 6)).pack(side='left', padx=1)
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Custom Register Read:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        cf = ttk.Frame(acc_f)
        cf.grid(row=r, column=0, columnspan=4, pady=2)
        ttk.Label(cf, text="Reg:").pack(side='left')
        self.acc_reg = ttk.Entry(cf, width=6)
        self.acc_reg.pack(side='left', padx=2)
        self.acc_reg.insert(0, '0x0F')
        ttk.Label(cf, text="Len:").pack(side='left')
        self.acc_len = ttk.Entry(cf, width=4)
        self.acc_len.pack(side='left', padx=2)
        self.acc_len.insert(0, '1')
        ttk.Button(cf, text="Read", command=lambda: self._accel_read(
            int(self.acc_reg.get(), 16), int(self.acc_len.get()))).pack(side='left', padx=4)
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Result:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        self.acc_result = tk.Text(acc_f, height=6, width=38, state='disabled')
        self.acc_result.grid(row=r, column=0, columnspan=4, pady=2)
        # Capture tab (replaces old Trigger tab)
        self._build_capture_tab(nb)

        # Decoder tab — filter + decoder configuration
        dec_f = ttk.Frame(nb, padding=5)
        nb.add(dec_f, text="Decode")
        row = 0
        ttk.Label(dec_f, text="Glitch threshold:").grid(row=row, column=0, sticky='w')
        self.dec_thresh = ttk.Combobox(dec_f, values=['2','3','5','0'], width=4, state='readonly')
        self.dec_thresh.set('3'); self.dec_thresh.grid(row=row, column=1, sticky='w')
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Label(dec_f, text="Filtered channels:").grid(row=row, column=0, columnspan=3, sticky='w')
        row += 1
        self.filter_vars = []
        f_ch_frame = ttk.Frame(dec_f)
        f_ch_frame.grid(row=row, column=0, columnspan=3, sticky='w')
        for ci in range(NUM_CHANNELS):
            var = tk.BooleanVar(value=False)
            self.filter_vars.append(var)
            ttk.Checkbutton(f_ch_frame, text=f"CH{ci}", variable=var).pack(side='left', padx=1)
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Label(dec_f, text="Decoders:").grid(row=row, column=0, columnspan=3, sticky='w')
        row += 1
        self.decoder_frame = ttk.Frame(dec_f)
        self.decoder_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
        self.decoder_ui = []
        for di in range(NUM_CHANNELS):
            self._add_decoder_ui(di)
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Button(dec_f, text="Apply Decoders", command=self._apply_decoders).grid(row=row, column=0, columnspan=2, pady=3)
        row += 1
        self.dec_out = tk.Text(dec_f, height=6, width=28, font=('Consolas', 7))
        self.dec_out.grid(row=row, column=0, columnspan=3, sticky='nsew')
        dec_f.columnconfigure(1, weight=1)

        # Export tab
        exp_f = ttk.Frame(nb, padding=5)
        nb.add(exp_f, text="Export")
        ttk.Button(exp_f, text="Save as .ols", command=self._export_ols, width=20).pack(pady=2)
        ttk.Button(exp_f, text="Save as .sr", command=self._export_sr, width=20).pack(pady=2)
        ttk.Button(exp_f, text="Copy to clipboard", command=self._export_clip, width=20).pack(pady=2)
        ttk.Separator(exp_f, orient='horizontal').pack(fill='x', pady=4)
        ttk.Button(exp_f, text="Export range (M1->M2)", command=self._export_marker_range, width=20).pack(pady=2)
        self.export_size_lbl = ttk.Label(exp_f, text="Captured: 0 samples (0 MB)", font=('Consolas', 8))
        self.export_size_lbl.pack(pady=2)

        # Data Logger tab
        log_f = ttk.Frame(nb, padding=5)
        nb.add(log_f, text="Data Logger")
        row = 0
        ttk.Label(log_f, text="Rate:").grid(row=row, column=0, sticky='w')
        self.log_rate = ttk.Combobox(log_f, values=['100kHz','500kHz','1MHz','2MHz','4MHz'],
                                     width=8, state='readonly')
        self.log_rate.set('1MHz'); self.log_rate.grid(row=row, column=1, sticky='w')
        ttk.Label(log_f, text="Samples:").grid(row=row, column=2, sticky='w')
        self.log_nsamp = ttk.Combobox(log_f, values=['512','1024','5000','10000'],
                                      width=6, state='readonly')
        self.log_nsamp.set('1024'); self.log_nsamp.grid(row=row, column=3, sticky='w')
        row += 1
        ttk.Label(log_f, text="Trigger mode:").grid(row=row, column=0, sticky='w')
        self.log_trig_mode = ttk.Combobox(log_f, values=['Off','Rising','Falling','Protocol'],
                                          width=10, state='readonly')
        self.log_trig_mode.set('Off'); self.log_trig_mode.grid(row=row, column=1, columnspan=3, sticky='w')
        row += 1
        ttk.Separator(log_f, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        ttk.Label(log_f, text="CSV file:").grid(row=row, column=0, sticky='w')
        self.log_csv_path_v = tk.StringVar(value='data_log.csv')
        ttk.Entry(log_f, textvariable=self.log_csv_path_v, width=22).grid(row=row, column=1, columnspan=2)
        ttk.Button(log_f, text="Browse", command=self._log_browse).grid(row=row, column=3, padx=2)
        row += 1
        ttk.Separator(log_f, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        self.log_count_label = ttk.Label(log_f, text="Captures: 0", font=('Consolas', 10, 'bold'))
        self.log_count_label.grid(row=row, column=0, columnspan=2, sticky='w')
        row += 1
        frm = ttk.Frame(log_f)
        frm.grid(row=row, column=0, columnspan=4, pady=4)
        self.log_arm_btn = ttk.Button(frm, text="Arm", command=self._logger_arm, width=8)
        self.log_arm_btn.pack(side='left', padx=2)
        self.log_stop_btn = ttk.Button(frm, text="Stop", command=self._logger_stop, width=8, state='disabled')
        self.log_stop_btn.pack(side='left', padx=2)
        row += 1
        self.log_status = ttk.Label(log_f, text="Idle", relief='sunken', anchor='w')
        self.log_status.grid(row=row, column=0, columnspan=4, sticky='ew', pady=2)
        row += 1
        self.log_out = tk.Text(log_f, height=6, width=32, font=('Consolas', 7))
        self.log_out.grid(row=row, column=0, columnspan=4, sticky='nsew')
        log_f.columnconfigure(1, weight=1)

    # ─── Capture Tab ─────────────────────────────────────────────

    MODE_OPTIONS = [
        '16 Digital', '16 Dig + 1 Ana', '16 Dig + 2 Ana',
        '1 Analog', '2 Analog', '4 Analog',
        '16 Dig + 4 Ana', '16 Dig + 8 Ana', '16 Dig + 2 Ana (alt)',
    ]

    ROLLING_READBACK_MB_PER_S = 30

    def _build_capture_tab(self, nb):
        cap_f = ttk.Frame(nb, padding=5)
        nb.add(cap_f, text="Capture")
        self.cap_frame = cap_f
        row = 0

        # ── Step 1: Mode ──
        ttk.Label(cap_f, text="Step 1: Mode", font=('', 9, 'bold')).grid(
            row=row, column=0, columnspan=4, sticky='w', pady=(0, 2))
        row += 1
        ttk.Label(cap_f, text="Mode:").grid(row=row, column=0, sticky='w')
        self.mode_cb = ttk.Combobox(cap_f, values=self.MODE_OPTIONS,
                                    width=16, state='readonly')
        self.mode_cb.set('16 Dig + 8 Ana')
        self.mode_cb.grid(row=row, column=1, columnspan=3, sticky='w', padx=2)
        self.mode_cb.bind('<<ComboboxSelected>>', self._mode_changed)
        row += 1

        # Analog channel selectors (A0-A7)
        ttk.Label(cap_f, text="Analog inputs:").grid(row=row, column=0, sticky='w')
        af = ttk.Frame(cap_f)
        af.grid(row=row, column=1, columnspan=3, sticky='w')
        self._analog_labels = {}
        self._analog_combos = {}
        for ai in range(8):
            lbl = ttk.Label(af, text=f"A{ai}:")
            cb = ttk.Combobox(af, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
            cb.set(str(ai))
            self._analog_labels[ai] = lbl
            self._analog_combos[ai] = cb
            # Pack A0-A1 visible by default; rest managed by _mode_changed
            if ai < 2:
                lbl.pack(side='left'); cb.pack(side='left', padx=1)
            else:
                lbl.pack_forget(); cb.pack_forget()
        row += 1

        # Rate info line
        self.rate_info_var = tk.StringVar(value='')
        self.rate_info_lbl = ttk.Label(cap_f, textvariable=self.rate_info_var,
                                       font=('Consolas', 7), foreground='#555')
        self.rate_info_lbl.grid(row=row, column=0, columnspan=4, sticky='w', pady=(2, 4))
        row += 1
        ttk.Separator(cap_f, orient='horizontal').grid(
            row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1

        # ── Step 2: Capture Type ──
        ttk.Label(cap_f, text="Step 2: Capture Type", font=('', 9, 'bold')).grid(
            row=row, column=0, columnspan=4, sticky='w', pady=(0, 2))
        row += 1
        self.capture_type = tk.StringVar(value='rolling')
        self.rolling_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(cap_f, text="Rolling (continuous, free-run)",
                        variable=self.capture_type, value='rolling',
                        command=self._capture_type_changed).grid(
            row=row, column=0, columnspan=4, sticky='w', padx=8)
        row += 1
        ttk.Radiobutton(cap_f, text="Single (triggered capture)",
                        variable=self.capture_type, value='single',
                        command=self._capture_type_changed).grid(
            row=row, column=0, columnspan=4, sticky='w', padx=8)
        row += 1
        ttk.Separator(cap_f, orient='horizontal').grid(
            row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1

        # ── Step 3: Trigger Section (shown only for Single) ──
        self.trig_frame = ttk.LabelFrame(cap_f, text="Step 3: Trigger Setup", padding=3)
        self.trig_frame.grid(row=row, column=0, columnspan=4, sticky='ew', pady=2)
        tr = 0
        ttk.Label(self.trig_frame, text="Mode:").grid(row=tr, column=0, sticky='w', pady=1)
        self.trig_mode = ttk.Combobox(self.trig_frame, values=['Off', 'Rising', 'Falling'],
                                      state='readonly', width=12)
        self.trig_mode.set('Off')
        self.trig_mode.grid(row=tr, column=1, sticky='w', pady=1)
        self.trig_mode.bind('<<ComboboxSelected>>', self._trig_mode_changed)
        tr += 1
        ttk.Separator(self.trig_frame, orient='horizontal').grid(
            row=tr, column=0, columnspan=4, sticky='ew', pady=2)
        tr += 1
        ttk.Label(self.trig_frame, text="Enable on channel:").grid(
            row=tr, column=0, columnspan=4, sticky='w')
        tr += 1
        self.trig_ch_vars = []
        tfc = ttk.Frame(self.trig_frame)
        tfc.grid(row=tr, column=0, columnspan=4, sticky='w')
        for i in range(NUM_CHANNELS):
            var = tk.BooleanVar(value=False)
            self.trig_ch_vars.append(var)
            cb = ttk.Checkbutton(tfc, text=f'CH{i}', variable=var, state='disabled')
            cb.pack(side='left', padx=1)
        tr += 1
        ttk.Separator(self.trig_frame, orient='horizontal').grid(
            row=tr, column=0, columnspan=4, sticky='ew', pady=2)
        tr += 1
        self.fast_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.trig_frame, text="Fast mode (120 MHz, 1024 samples max)",
                        variable=self.fast_mode_var).grid(
            row=tr, column=0, columnspan=4, sticky='w', pady=1)
        tr += 1
        self.debug_ch0_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.trig_frame, text="Drive CH0 debug square wave",
                        variable=self.debug_ch0_var,
                        command=self._debug_ch0_changed).grid(
            row=tr, column=0, columnspan=4, sticky='w', pady=1)
        tr += 1
        ttk.Label(self.trig_frame,
            text="WARNING: CH0 becomes FPGA output when enabled.",
            foreground='red', font=('Consolas', 7)).grid(
            row=tr, column=0, columnspan=4, sticky='w')
        tr += 1
        self.raw_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.trig_frame, text="Raw mode (8 ch, higher throughput)",
                        variable=self.raw_mode_var).grid(
            row=tr, column=0, columnspan=4, sticky='w', pady=1)
        tr += 1
        self.schmitt_var = tk.BooleanVar(value=False)
        self.schmitt_thresh_var = tk.StringVar(value='3')
        sf = ttk.Frame(self.trig_frame)
        sf.grid(row=tr, column=0, columnspan=4, sticky='w', pady=1)
        ttk.Checkbutton(sf, text="Schmitt trigger (glitch filter)",
                        variable=self.schmitt_var,
                        command=self._apply_schmitt).pack(side='left')
        ttk.Label(sf, text="Thresh:").pack(side='left', padx=(8, 2))
        ttk.Spinbox(sf, from_=1, to=7, width=3, textvariable=self.schmitt_thresh_var,
                    command=self._apply_schmitt).pack(side='left')
        tr += 1
        ttk.Separator(self.trig_frame, orient='horizontal').grid(
            row=tr, column=0, columnspan=4, sticky='ew', pady=2)
        tr += 1
        ttk.Label(self.trig_frame, text="Protocol Trigger:").grid(
            row=tr, column=0, columnspan=4, sticky='w')
        tr += 1
        pf = ttk.Frame(self.trig_frame)
        pf.grid(row=tr, column=0, columnspan=4, sticky='w')
        self.proto_trig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(pf, text="Enable", variable=self.proto_trig_var).pack(side='left')
        ttk.Label(pf, text="Match (hex):").pack(side='left', padx=(8, 2))
        self.proto_match = ttk.Entry(pf, width=6)
        self.proto_match.insert(0, '0x57')
        self.proto_match.pack(side='left')
        pf2 = ttk.Frame(self.trig_frame)
        pf2.grid(row=tr+1, column=0, columnspan=4, sticky='w')
        ttk.Label(pf2, text="UART Ch:").pack(side='left')
        self.proto_ch = ttk.Combobox(pf2, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.proto_ch.set('0')
        self.proto_ch.pack(side='left', padx=2)
        ttk.Label(pf2, text="Baud:").pack(side='left')
        self.proto_baud = ttk.Entry(pf2, width=10)
        self.proto_baud.insert(0, '115200')
        self.proto_baud.pack(side='left', padx=2)
        tr += 2

        # Hide trigger section initially (default is rolling)
        self.trig_frame.grid_remove()
        row += 1

        # ── Channel Visibility ──
        ttk.Separator(cap_f, orient='horizontal').grid(
            row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        ttk.Label(cap_f, text="Channels", font=('', 9, 'bold')).grid(
            row=row, column=0, columnspan=4, sticky='w')
        row += 1
        self.ch_vis_frame = ttk.LabelFrame(cap_f, text="Toggle channel visibility", padding=3)
        self.ch_vis_frame.grid(row=row, column=0, columnspan=4, sticky='ew', pady=2)
        vis_row = 0
        self.ch_vis_vars = []
        # Digital channels
        for i in range(0, NUM_CHANNELS, 8):
            vf = ttk.Frame(self.ch_vis_frame)
            vf.pack(fill='x')
            for j in range(i, min(i + 8, NUM_CHANNELS)):
                var = tk.BooleanVar(value=True)
                self.ch_vis_vars.append(var)
                cb = ttk.Checkbutton(vf, text=f"CH{j}", variable=var,
                                     command=lambda ci=j: self._toggle_ch_vis(ci))
                cb.pack(side='left', padx=1)
        # Analog channels
        af2 = ttk.Frame(self.ch_vis_frame)
        af2.pack(fill='x')
        self.ana_vis_vars = []
        for ai in range(8):
            var = tk.BooleanVar(value=True)
            self.ana_vis_vars.append(var)
            cb = ttk.Checkbutton(af2, text=f"A{ai}", variable=var,
                                 command=lambda ai=ai: self._toggle_ana_vis(ai))
            cb.pack(side='left', padx=1)
        row += 1

        # ── Progress bar ──
        ttk.Separator(cap_f, orient='horizontal').grid(
            row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        self.live_bar = ttk.Progressbar(cap_f, length=280, mode='determinate', value=0)
        self.live_bar.grid(row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1

        cap_f.columnconfigure(1, weight=1)

    def _capture_type_changed(self):
        is_rolling = self.capture_type.get() == 'rolling'
        self.rolling_var.set(is_rolling)
        if is_rolling:
            self.trig_frame.grid_remove()
        else:
            self.trig_frame.grid()
        self._update_rate_info()

    def _toggle_ch_vis(self, idx):
        if hasattr(self, 'wave') and idx < len(self.wave.channel_visible):
            self.wave.channel_visible[idx] = self.ch_vis_vars[idx].get()
            self.wave.redraw()

    def _toggle_ana_vis(self, idx):
        ana_offset = NUM_CHANNELS + idx  # analog channels after digital
        if hasattr(self, 'wave') and ana_offset < len(self.wave.channel_visible):
            self.wave.channel_visible[ana_offset] = self.ana_vis_vars[idx].get()
            self.wave.redraw()

    def _on_rate_changed(self, event=None):
        raw = self.rate_cb.get()
        self._apply_rate(raw)
        self._update_buf_presets()

    def _apply_rate(self, raw):
        """Parse, clamp, and apply rate. Returns rate in Hz."""
        max_rate = self._get_max_rate()
        try:
            if raw.lower() == 'fast 120mhz':
                rate = 120_000_000
                self.fast_mode_var.set(True)
                self._nsamp = 1024
                self.rate_cb.set('Fast 120MHz')
            else:
                if self.fast_mode_var.get():
                    self.fast_mode_var.set(False)
                raw = raw.replace(',', '').strip()
                if raw.lower().endswith('mhz'):
                    rate = int(float(raw.lower().replace('mhz', '')) * 1_000_000)
                elif raw.lower().endswith('khz'):
                    rate = int(float(raw.lower().replace('khz', '')) * 1_000)
                elif raw.lower().endswith('hz'):
                    rate = int(float(raw.lower().replace('hz', '')))
                else:
                    rate = int(float(raw)) if raw else 1_000_000
        except (ValueError, AttributeError):
            rate = 1_000_000
        rate = max(1, min(rate, max_rate))
        self.rate_cb.set(self._fmt_rate(rate))
        self._update_time_display()
        self._update_rate_info()
        self._update_buf_estimate()
        return rate

    def _fmt_rate(self, rate_hz):
        if rate_hz >= 120_000_000:
            return 'Fast 120MHz'
        if rate_hz >= 1_000_000:
            return f'{rate_hz / 1_000_000:.3g}MHz'.replace('.0', '')
        if rate_hz >= 1_000:
            return f'{rate_hz / 1_000:.3g}kHz'.replace('.0', '')
        return f'{rate_hz}Hz'

    def _get_max_rate(self):
        """Return the maximum allowed rate based on mode and capture type."""
        mode = self._get_capture_mode()
        max_rate = 96_000_000  # sysclk limit
        if self.capture_type.get() == 'rolling':
            stride = analog_frame_stride(mode)
            rolling_limit = int(self.ROLLING_READBACK_MB_PER_S * 1_000_000 / stride)
            max_rate = min(max_rate, rolling_limit)
        return max_rate

    def _update_rate_info(self):
        rate = self._get_rate()
        mode = self._get_capture_mode()
        stride = analog_frame_stride(mode)
        mb_per_s = rate * stride / 1_000_000
        max_rate = self._get_max_rate()
        if self.capture_type.get() == 'rolling':
            rolling_max = int(self.ROLLING_READBACK_MB_PER_S * 1_000_000 / stride)
            if rate > rolling_max:
                self.rate_info_var.set(
                    f"{self._fmt_rate(rate)} → {mb_per_s:.1f} MB/s  "
                    f"⚠ Rolling limited to {self._fmt_rate(rolling_max)} — use Single"
                )
                self.rate_info_lbl.configure(foreground='#c00')
            else:
                self.rate_info_var.set(
                    f"{self._fmt_rate(rate)} → {mb_per_s:.1f} MB/s  |  OK for rolling"
                )
                self.rate_info_lbl.configure(foreground='#555')
        else:
            self.rate_info_var.set(
                f"{self._fmt_rate(rate)} → {mb_per_s:.1f} MB/s  |  Single-shot OK"
            )
            self.rate_info_lbl.configure(foreground='#555')

    def _update_buf_presets(self, event=None):
        """Regenerate buffer combobox values with MB sizes based on current rate+mode."""
        rate = self._get_rate()
        stride = analog_frame_stride(self._get_capture_mode())
        presets_ms = [1, 10, 50, 100, 500, 1000]
        labels = []
        for ms in presets_ms:
            nsamp = int(ms / 1000 * rate)
            mb = nsamp * stride / (1024 * 1024)
            labels.append(f'{ms} ms ({mb:.1f} MB)')
        labels.append('Custom')
        self.rolling_buf['values'] = labels
        self._update_buf_estimate()

    # ─── UI Actions ────────────────────────────────────────────

    def _scan_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb['values'] = ports
        if ports and not self.port_cb.get():
            self.port_cb.set(ports[0])
        self.status['text'] = f"Found {len(ports)} port(s)"

    def _auto_connect(self):
        """Auto-detect and connect to OLS device via SPI."""
        if find_spi_device():
            self._connect()
            return
        self.status['text'] = "No SPI device found — connect manually"
        self._update_ui_state(connected=False)

    def _connect(self):
        try:
            self.dev = OLSDeviceSPI()
            self.dev.open()
            label = "SPI @ 30 MHz"
            # Verify device responds
            self.dev.reset()
            meta = self.dev.get_metadata()
            # Apply GUI debug CH0 state to hardware after reset
            self._apply_debug_ch0_setting()
            if hasattr(self.dev, 'set_debug_ch0'):
                self.dev.set_debug_ch0(self.debug_ch0_var.get())
            dbg = f"[DBG] Connected backend={self._backend} meta={len(meta)}B"
            if hasattr(self.dev, 'spi') and self.dev.spi:
                q = self.dev.spi.dev.getQueueStatus() if hasattr(self.dev.spi, 'dev') else 0
                dbg += f" queue={q}"
            print(dbg)
            if len(meta) == 0:
                self.dev.close()
                self.dev = None
                raise RuntimeError("FPGA not responding — need spi-focus firmware. Program the MAX1000 with the bitstream from this branch (hdl/proj/).")
            self.status['text'] = f"Connected via {label} (meta: {len(meta)}B)"
            self._update_ui_state(connected=True)
        except Exception as e:
            self.status['text'] = f"Connect failed: {e}"
            messagebox.showerror("Connect Error", str(e))

    def _debug_ch0_changed(self):
        enable = self.debug_ch0_var.get()
        if not self.dev:
            return
        is_live = getattr(self, 'capture_running', False) and hasattr(self.dev, '_pending_debug_enable')
        if is_live:
            self.dev.debug_ch0_enabled = enable
            self.dev._pending_debug_enable = enable
        elif hasattr(self.dev, 'set_debug_ch0'):
            try:
                self.dev.set_debug_ch0(enable)
            except Exception as e:
                self.status['text'] = f"CH0 debug update failed: {e}"

    def _apply_schmitt(self):
        if not self.dev or not hasattr(self.dev, 'set_schmitt'):
            return
        try:
            enable = self.schmitt_var.get()
            thresh = int(self.schmitt_thresh_var.get())
            is_live = getattr(self, 'capture_running', False) and hasattr(self.dev, '_pending_schmitt_enable')
            if is_live:
                self.dev._pending_schmitt_enable = enable
                self.dev._pending_schmitt_threshold = thresh
            else:
                self.dev.set_schmitt(enable, thresh)
        except Exception as e:
            self.status['text'] = f"Schmitt trigger update failed: {e}"

    def _apply_debug_ch0_setting(self):
        if self.dev and hasattr(self, 'debug_ch0_var') and hasattr(self.dev, 'debug_ch0_enabled'):
            self.dev.debug_ch0_enabled = self.debug_ch0_var.get()

    def _disconnect(self):
        if self.dev:
            self.dev.close()
            self.dev = None
        self._update_ui_state(connected=False)
        self.status['text'] = "Disconnected"

    def _update_ui_state(self, connected=True):
        if hasattr(self, 'port_frame') and hasattr(self, 'port_sep'):
            if connected:
                self.port_frame.pack_forget()
                self.port_sep.pack_forget()
            else:
                self.port_frame.pack(side='left')
                self.port_sep.pack(side='left', fill='y', padx=5)

    def _mode_changed(self, event=None):
        """Show/hide analog channel selectors based on current mode."""
        mode = self._get_capture_mode()
        ana_count = 0
        if mode & ANALOG_ENABLE_BIT:
            ana_count = 8
        elif mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_ANALOG1):
            ana_count = 1
        elif mode in (ANALOG_MODE_MIXED2, ANALOG_MODE_ANALOG2, ANALOG_MODE_MIXED_DUAL):
            ana_count = 2
        elif mode in (ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4):
            ana_count = 4
        for ai in range(8):
            if ai < ana_count:
                self._analog_labels[ai].pack(side='left')
                self._analog_combos[ai].pack(side='left', padx=1)
            else:
                self._analog_labels[ai].pack_forget()
                self._analog_combos[ai].pack_forget()
        self._update_rate_info()
        self._update_buf_presets()

    def _add_decoder_ui(self, di):
        """Create a decoder slot UI in the decoder_frame."""
        frame = ttk.LabelFrame(self.decoder_frame, text=f"Decoder {di+1}", padding=3)
        frame.pack(fill='x', pady=1)
        vars_d = {}
        # Enable checkbox
        var_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Enable", variable=var_en).pack(anchor='w')
        vars_d['en'] = var_en
        # Row: source channel
        r1 = ttk.Frame(frame)
        r1.pack(fill='x')
        ttk.Label(r1, text="Src CH:").pack(side='left')
        var_src = ttk.Combobox(r1, values=list(range(NUM_CHANNELS)) + [f"{i}_f" for i in range(NUM_CHANNELS)], width=5, state='readonly')
        var_src.set('0'); var_src.pack(side='left', padx=2)
        vars_d['src'] = var_src
        ttk.Label(r1, text="Proto:").pack(side='left')
        var_proto = ttk.Combobox(r1, values=['UART','I2C','SPI'], width=6, state='readonly')
        var_proto.set('UART'); var_proto.pack(side='left', padx=2)
        vars_d['proto'] = var_proto
        # Row: protocol params
        r2 = ttk.Frame(frame)
        r2.pack(fill='x')
        ttk.Label(r2, text="Baud:").pack(side='left')
        var_baud = ttk.Entry(r2, width=10)
        var_baud.insert(0, '115200'); var_baud.pack(side='left', padx=2)
        vars_d['baud'] = var_baud
        ttk.Label(r2, text="Threshold:").pack(side='left')
        var_th = ttk.Combobox(r2, values=['0','2','3','5'], width=3, state='readonly')
        var_th.set('3'); var_th.pack(side='left', padx=2)
        vars_d['thresh'] = var_th
        # I2C channel selectors (shown/hidden by proto)
        var_sda_lbl = ttk.Label(r2, text="SDA:")
        var_sda = ttk.Combobox(r2, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        var_sda.set('3')
        var_scl_lbl = ttk.Label(r2, text="SCL:")
        var_scl = ttk.Combobox(r2, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        var_scl.set('1')
        vars_d['sda'] = var_sda; vars_d['scl'] = var_scl
        vars_d['sda_lbl'] = var_sda_lbl; vars_d['scl_lbl'] = var_scl_lbl
        # Proto change → show/hide I2C fields
        def _on_proto_change(*_):
            p = var_proto.get()
            if p == 'I2C':
                var_sda_lbl.pack(side='left', padx=(4,0)); var_sda.pack(side='left', padx=1)
                var_scl_lbl.pack(side='left', padx=(4,0)); var_scl.pack(side='left', padx=1)
                var_baud.pack_forget(); ttk.Label(r2, text="Baud:").pack_forget()
            else:
                var_sda_lbl.pack_forget(); var_sda.pack_forget()
                var_scl_lbl.pack_forget(); var_scl.pack_forget()
                ttk.Label(r2, text="Baud:").pack(side='left'); var_baud.pack(side='left', padx=2)
        var_proto.bind('<<ComboboxSelected>>', _on_proto_change)
        self.decoder_ui.append((frame, vars_d))

    def _apply_decoders(self):
        """Read UI state into self.decoder_slots + filter config, rebuild channels."""
        self.filter_threshold = int(self.dec_thresh.get())
        self.filter_enabled = [v.get() for v in self.filter_vars]
        slots = []
        for di, (frame, vd) in enumerate(self.decoder_ui):
            if not vd['en'].get():
                continue
            src = vd['src'].get()
            proto = vd['proto'].get()
            slot = {
                'enabled': True,
                'src_str': src,
                'src_idx': int(src) if src.isdigit() else int(src[0]),
                'src_is_filtered': '_f' in src,
                'proto': proto,
                'baud': int(vd['baud'].get()) if proto != 'I2C' else 0,
                'thresh': int(vd['thresh'].get()),
                'sda_idx': int(vd['sda'].get()),
                'scl_idx': int(vd['scl'].get()),
                'frames': [],
                'sig': [],
            }
            slots.append(slot)
        self.decoder_slots = slots
        if self.ch_data:
            self._process_decoders()
            self.wave.redraw()

    def _get_rate(self):
        try:
            rate_str = self.rate_cb.get()
            return max(1, int(rate_str.replace('kHz','000').replace('MHz','000000')))
        except (ValueError, AttributeError):
            return 1_000_000

    def _get_samples(self):
        return getattr(self, '_nsamp', 5000)

    def _get_capture_mode(self):
        mode_map = {
            '16 Digital': ANALOG_MODE_DIGITAL8,
            '16 Dig + 1 Ana': ANALOG_MODE_MIXED1,
            '16 Dig + 2 Ana': ANALOG_MODE_MIXED2,
            '1 Analog': ANALOG_MODE_ANALOG1,
            '2 Analog': ANALOG_MODE_ANALOG2,
            '4 Analog': ANALOG_MODE_ANALOG4,
            '16 Dig + 4 Ana': ANALOG_MODE_MIXED2_4,
            '16 Dig + 8 Ana': ANALOG_ENABLE_BIT,
            '16 Dig + 2 Ana (alt)': ANALOG_MODE_MIXED_DUAL,
        }
        return mode_map.get(self.mode_cb.get(), ANALOG_MODE_DIGITAL8)

    def _update_time_display(self, event=None):
        rate = self._get_rate()
        ns = self._get_samples()
        t_sec = ns / rate if rate > 0 else 0
        if t_sec < 0.001:
            text = f"{t_sec*1e6:.1f} us"
        elif t_sec < 1:
            text = f"{t_sec*1e3:.3f} ms"
        else:
            text = f"{t_sec:.3f} s"
        self.time_var.set(text)

    def _parse_buf_ms(self, raw):
        """Extract ms value from e.g. '100 ms (1.7 MB)' or '100'."""
        raw = raw.strip()
        if 'ms' in raw:
            raw = raw.split('ms')[0].strip()
        try:
            return float(raw)
        except ValueError:
            return 100.0

    def _update_buf_estimate(self, event=None):
        raw = self.rolling_buf_var.get()
        ms = self._parse_buf_ms(raw)
        if ms > 0:
            rate = self._get_rate()
            nsamp = int(ms / 1000 * rate)
            stride = analog_frame_stride(self._get_capture_mode())
            mem_mb = nsamp * stride / (1024 * 1024)
            self.buf_estimate_lbl['text'] = (
                f"~{ms:.0f} ms @ {self._fmt_rate(rate)}, ~{mem_mb:.1f} MB"
            )
        else:
            self.buf_estimate_lbl['text'] = ""

    def _on_rolling_buf_change(self, event=None):
        """When buffer size changes during active rolling, restart capture with new size."""
        if self.capture_running and self.capture_type.get() == 'rolling':
            raw = self.rolling_buf_var.get()
            new_ms = self._parse_buf_ms(raw)
            if new_ms < 1:
                return
            rate = self._get_rate()
            new_ns = int(new_ms / 1000 * rate)
            old_ns = self.capture_window
            if new_ns == old_ns or new_ns < 100:
                return
            self._stop_capture()
            self.win.after(300, self._capture)

    def _time_changed(self, event=None):
        try:
            raw = self.time_var.get().strip()
            t_sec = None
            if raw.endswith('us'):
                t_sec = float(raw[:-2]) / 1e6
            elif raw.endswith('ms'):
                t_sec = float(raw[:-2]) / 1e3
            elif raw.endswith('s'):
                t_sec = float(raw[:-1])
            else:
                t_sec = float(raw)
            rate = self._get_rate()
            ns = int(t_sec * rate)
            samp_vals = [int(v) for v in ['500','1000','5000','10000','50000','100000','200000','500000']]
            # find closest
            self._nsamp = max(2, min(ns, 500000))
            self._update_time_display()
        except:
            self._update_time_display()  # revert to computed time

    def _capture(self):
        if not self.dev:
            messagebox.showerror("Error", "Not connected")
            return
        if self.capture_running:
            if self.stop_evt.is_set() or self._pending_restart:
                self.status['text'] = "Waiting for previous capture to finish..."
                self.win.after(500, self._capture)
            return
        # Clean device state before starting any capture
        if self.dev and hasattr(self.dev, 'reset'):
            self.dev.reset()
        rate = self._get_rate()
        nsamp = self._get_samples()
        fast = self.fast_mode_var.get()
        rolling = self.capture_type.get() == 'rolling'
        if fast and rolling:
            self.status['text'] = "Fast mode incompatible with rolling — disabling fast mode"
            fast = False
            self.fast_mode_var.set(False)
        if fast:
            rate = min(rate, 120_000_000)
            nsamp = min(nsamp, 1024)
        # Build trigger mask from UI
        trig_mode_val = self.trig_mode.get()
        if trig_mode_val == 'Off':
            trigger = None
        else:
            mode_bits = {'Rising': 1, 'Falling': 2}[trig_mode_val] << 30
            ch_mask = sum((1 << i) for i, v in enumerate(self.trig_ch_vars) if v.get())
            if ch_mask == 0:
                ch_mask = 1  # default to CH0 if none selected
            trigger = mode_bits | ch_mask
        # Blank the waveform canvas for live point-by-point drawing
        self.wave.ch_data = [[] for _ in range(NUM_CHANNELS)]
        self.wave.num_samples = 0
        self.wave._drawn_to = 0
        self.wave.delete('all')
        self.capture_mode = self._get_capture_mode()
        self.analog_ch0_sel = int(self._analog_combos[0].get())
        self.analog_ch1_sel = int(self._analog_combos[1].get())
        self.analog_ch2_sel = int(self._analog_combos[2].get())
        self.analog_ch3_sel = int(self._analog_combos[3].get())
        self.analog_ch4_sel = int(self._analog_combos[4].get())
        self.analog_ch5_sel = int(self._analog_combos[5].get())
        self.analog_ch6_sel = int(self._analog_combos[6].get())
        self.analog_ch7_sel = int(self._analog_combos[7].get())
        if self.capture_mode != ANALOG_MODE_DIGITAL8:
            self.capture_stride = analog_frame_stride(self.capture_mode)
        else:
            self.capture_stride = 4  # FPGA always sends 4-byte samples
        self.capture_nsamp = nsamp
        if rolling:
            try:
                buf_ms = self._parse_buf_ms(self.rolling_buf_var.get())
            except:
                buf_ms = 100.0
            self.capture_window = max(100, int(buf_ms / 1000 * rate))
        w = max(100, self.wave.winfo_width() - self.wave.LABEL_WIDTH)
        self.wave.px_scale = w / max(1, nsamp)
        self.wave.scroll_x = 0
        self.stop_evt.clear()
        self.capture_running = True
        self.capture_progress = (0, nsamp)
        self.capture_result = None
        self.stop_btn.configure(state='normal')
        self.status['text'] = f"Capturing {nsamp} samples at {rate/1e6:.1f} MHz..."
        self.win.update()

        # Read protocol trigger settings
        proto_enable = self.proto_trig_var.get()
        if proto_enable:
            try:
                match_byte = int(self.proto_match.get(), 16) & 0xFF
            except:
                match_byte = 0x57
            proto_ch = int(self.proto_ch.get())
            try:
                proto_baud = int(self.proto_baud.get())
            except:
                proto_baud = 115200

        def thread_fn():
            try:
                if fast:
                    self.dev.fast_mode(True)
                raw = self.raw_mode_var.get()
                if hasattr(self.dev, 'raw_mode') and not hasattr(self.dev, 'pkt'):
                    self.dev.raw_mode(raw)
                else:
                    self.dev._stride = 1 if raw else 4
                    self.dev._raw_flags = 0
                self._apply_schmitt()
                if proto_enable:
                    self.dev.trigger_decode(match_byte=match_byte, channel=proto_ch, baud=proto_baud, enable=True)
                if rolling:
                    buf_nsamp = self.capture_window
                    self.captured_bytes = bytearray()
                    ana = self.capture_mode != ANALOG_MODE_DIGITAL8
                    if ana:
                        self.dev.set_analog_config(self.capture_mode,
                            self.analog_ch0_sel, self.analog_ch1_sel)
                        as_ = analog_frame_stride(self.capture_mode)
                    else:
                        as_ = self.dev._stride
                    # Pass any pending generator data into rolling capture
                    pending = getattr(self.dev, '_pending_gen', None)
                    gen_kwargs = {}
                    if pending and isinstance(pending, dict):
                        gen_kwargs = {
                            'gen_data': pending.get('data', b''),
                            'gen_baud': pending.get('baud', 115200),
                            'gen_tx_pin': pending.get('tx_pin', 3),
                        }
                        self.dev._pending_gen = None
                    gen = self.dev.rolling_capture(
                        rate_hz=rate, chunk_nsamp=1024, buffer_nsamp=buf_nsamp,
                        stop_evt=self.stop_evt, progress_cb=None,
                        full_out=self.captured_bytes, stride=as_,
                        **gen_kwargs
                    )
                    for buf, got, total in gen:
                        self.capture_partial = buf
                        self.capture_progress = (got, total)
                        if ana:
                            frames = decode_analog_frames(buf, self.capture_mode)
                            self.capture_result = (buf, rate, got, as_, frames, self.capture_mode)
                        else:
                            stride = self.dev._stride
                            self.capture_result = (buf, rate, got, stride)
                else:
                    use_capture_analog = (self.capture_mode != ANALOG_MODE_DIGITAL8
                                          and not (self.capture_mode & ANALOG_ENABLE_BIT)
                                          and hasattr(self.dev, 'capture_analog'))
                    if use_capture_analog:
                        data, frames = self.dev.capture_analog(
                            rate_hz=rate, frames=nsamp, mode=self.capture_mode,
                            ch0=self.analog_ch0_sel, ch1=self.analog_ch1_sel,
                            timeout=max(3, nsamp // 10000 + 2),
                            stop_evt=self.stop_evt
                        )
                        self.capture_result = (data, rate, nsamp, self.capture_stride, frames, self.capture_mode)
                    elif self.capture_mode & ANALOG_ENABLE_BIT:
                        stride = analog_frame_stride(self.capture_mode)
                        self.dev.set_analog_config(self.capture_mode,
                            self.analog_ch0_sel, self.analog_ch1_sel)
                        sdram_words = nsamp * (stride // 2)
                        data = self.dev.capture(
                            rate_hz=rate * (stride // 2), nsamples=sdram_words,
                            timeout=max(3, sdram_words // 10000 + 2),
                            progress_cb=self._capture_progress,
                            trigger=trigger, stop_evt=self.stop_evt
                        )
                        trimmed = data[:nsamp * stride]
                        frames = decode_analog_frames(trimmed, self.capture_mode)
                        self.capture_result = (trimmed, rate, nsamp, stride, frames, self.capture_mode)
                    else:
                        need_bytes = nsamp * getattr(self.dev, '_stride', 4)
                        print(f"[DBG] capture rate={rate} nsamp={nsamp} expect_bytes={need_bytes} trigger={trigger}")
                        data = self.dev.capture(
                            rate_hz=rate, nsamples=nsamp,
                            timeout=max(3, nsamp//10000 + 2),
                            progress_cb=self._capture_progress,
                            trigger=trigger,
                            stop_evt=self.stop_evt
                        )
                        print(f"[DBG] capture returned {len(data)} bytes")
                        if len(data) >= 8:
                            print(f"[DBG] first 8 bytes hex: {data[:8].hex()}")
                        self.capture_result = (data, rate, nsamp)
            except Exception as e:
                self.capture_result = e
            finally:
                if fast:
                    try: self.dev.fast_mode(False)
                    except: pass
                self.capture_running = False

        t = threading.Thread(target=thread_fn, daemon=True)
        t.start()
        self._poll_capture()

    def _capture_progress(self, partial_data, got, total):
        self.capture_progress = (got, total)
        self.capture_partial = partial_data

    def _update_gen_buttons(self):
        """Grey out Send+Capture during rolling; update Send label."""
        if self.capture_running and self.capture_type.get() == 'rolling':
            self.gen_send_cap_btn.configure(state='disabled')
            self.gen_send_btn.configure(text='Send → rolling')
        else:
            self.gen_send_cap_btn.configure(state='normal')
            self.gen_send_btn.configure(text='Send')

    def _stop_capture(self):
        """Abort the running capture and return partial data."""
        if not self.capture_running:
            return
        self.stop_evt.set()
        if self.dev:
            try: self.dev.reset()
            except: pass
        self.capture_nsamp = 0  # Fit will use actual received count
        self.stop_btn.configure(state='disabled')
        self.status['text'] = "Capture stopped by user"

    def _poll_capture(self):
        self._update_gen_buttons()
        if self.capture_running:
            got, total = self.capture_progress
            if total > 0:
                if self.capture_type.get() == 'rolling':
                    buf_pct = min(got, total) / total * 100
                    total_got = int(got)
                    mem_bytes = len(getattr(self, 'captured_bytes', b''))
                    mem_mb = mem_bytes / (1024 * 1024)
                    self.status['text'] = f"Rolling: {buf_pct:.0f}% buffer — {total_got:,} samples ({mem_mb:.1f} MB)"
                    self._update_export_size_label()
                else:
                    pct = got / total * 100
                    self.status['text'] = f"Capturing... {got}/{total} ({pct:.0f}%)"
                # Update live waveform every poll (150ms) as data arrives
                self._live_waveform(got)
            self.win.after(150, self._poll_capture)
        elif self.capture_result is not None:
            if isinstance(self.capture_result, Exception):
                self.status['text'] = f"Capture error: {self.capture_result}"
                self.capture_running = False
                self.stop_btn.configure(state='disabled')
                return
            res = self.capture_result
            if len(res) == 4:
                data, rate, got, stride = res  # rolling mode
                self._load_capture(data, rate, stride)
                self._update_export_size_label()
            elif len(res) == 6:
                data, rate, nsamp, stride, frames, mode = res
                self._load_analog_capture(data, rate, frames, mode, stride)
            else:
                data, rate, nsamp = res  # normal mode
                stride = getattr(self.dev, '_stride', 4) if self.dev else 4
                self._load_capture(data, rate, stride)
            self._update_export_size_label()
            self.capture_running = False
            self.stop_btn.configure(state='disabled')
            # Pending restart (buffer size changed during rolling)
            if self._pending_restart:
                self._pending_restart = False
                self._capture()

    def _live_waveform(self, samples_so_far):
        """Render partial waveform — throttled to ~5fps, with visual loading bar."""
        partial = getattr(self, 'capture_partial', None)
        if partial is None or len(partial) < 4:
            return
        ana = self.capture_mode != ANALOG_MODE_DIGITAL8
        if ana:
            frames = decode_analog_frames(partial, self.capture_mode)
            ns = len(frames)
            if ns < 1:
                return
            num_ch = 16  # show at least 16 digital channels
            ch_partial = [[] for _ in range(num_ch)]
            for fr in frames:
                d = fr.get('digital', 0)
                for c in range(num_ch):
                    ch_partial[c].append((d >> c) & 1 if d is not None else 0)
            # Append analog series
            if frames and frames[0].get('adc'):
                for ai in range(len(frames[0]['adc'])):
                    ch_partial.append([fr['adc'][ai] for fr in frames])
        else:
            stride = getattr(self, 'capture_stride', 4)
            raw = getattr(self, 'raw_mode_var', None) and self.raw_mode_var.get()
            if raw and stride == 4:
                trimmed = len(partial) - (len(partial) % 4)
                partial = bytes(partial[i] for i in range(0, trimmed, 4)) if trimmed else b''
                stride = 1
            ch_partial, ns = samples_to_channels(partial, stride=stride)
        if ns < 2:
            return
        if ns == self.wave.num_samples and not (self.capture_running and self.capture_type.get() == 'rolling'):
            return

        # Always update the data model
        self.wave.ch_data = ch_partial
        self.wave.num_samples = ns

        # Rolling auto-scroll and memory guard (update regardless of redraw)
        if self.capture_running and self.capture_type.get() == 'rolling':
            ww = self.wave.winfo_width()
            vis = ww / self.wave.px_scale if self.wave.px_scale > 0 else self.wave.num_samples
            self.wave.scroll_x = max(0, ns - vis)
            max_keep = self.capture_window * 2 if self.capture_window > 0 else 100000
            if ns > max_keep:
                trim = ns - max_keep
                for ci in range(len(self.wave.ch_data)):
                    self.wave.ch_data[ci] = self.wave.ch_data[ci][trim:]
                self.wave.num_samples = max_keep

        # Apply filters + decoders to partial data
        self.ch_data = self.wave.ch_data
        self._process_decoders()

        # Throttle Canvas redraw to ~5fps (200ms min interval)
        now = time.time()
        dt = now - self._last_live_redraw
        always_draw = getattr(self, 'force_redraw', False)
        if dt >= 0.2 or always_draw:
            self.force_redraw = False
            self.live_bar['value'] = 0
            self.live_bar.configure(mode='determinate')
            self.wave.redraw()
            self._last_live_redraw = now
            self.live_bar['value'] = 100
            self.live_bar.update()
        else:
            # Visual feedback: progress bar fills across the 200ms window
            self.live_bar['value'] = min(99, dt / 0.2 * 100)

    def _load_capture(self, data, rate, stride=4):
        """Load captured data into the waveform view."""
        if not data:
            print("[DBG] _load_capture: data is empty")
            self.status['text'] = "Capture returned 0 bytes — FPGA not responding"
            return
        # Raw mode: display-only, extract first byte of each 4-byte word
        raw = getattr(self, 'raw_mode_var', None) and self.raw_mode_var.get()
        if raw and stride == 4:
            trimmed = len(data) - (len(data) % 4)
            data = bytes(data[i] for i in range(0, trimmed, 4)) if trimmed else b''
            stride = 1
        ch_data, ns = samples_to_channels(data, stride=stride)
        if ns == 0 or not ch_data or not ch_data[0]:
            self.status['text'] = "No samples decoded from capture"
            return
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        print(f"[DBG] _load_capture: {len(data)}B {ns}samples {trans}CH0trans")
        if ns > 0:
            print(f"[DBG] CH0 first 20: {''.join(str(ch0[i]) for i in range(min(20, ns)))}")
            print(f"[DBG] raw first 16B hex: {data[:16].hex()}")
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self._process_decoders()
        self.wave.load(self.ch_data, self.ch_names, self.samplerate)
        self._fit_view()  # ensure full waveform fits after capture completes
        self.status['text'] = f"Captured {len(data)} bytes ({ns} samples, {trans} CH0 transitions)"

    def _load_analog_capture(self, data, rate, frames, mode, stride):
        if not data:
            self.status['text'] = "Analog capture returned 0 bytes"
            return
        rows = frames if isinstance(frames, list) else decode_analog_frames(data, mode)
        self.last_analog_frames = rows
        digital = [[] for _ in range(NUM_CHANNELS)]
        analog_series = []
        analog_count = 0
        if mode & ANALOG_ENABLE_BIT:
            analog_count = 8
        elif mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2, ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2,
                      ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4, ANALOG_MODE_MIXED_DUAL):
            analog_count = 1 if mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_ANALOG1) else 2
            if mode in (ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4):
                analog_count = 4
            elif mode == ANALOG_MODE_MIXED_DUAL:
                analog_count = 2
        if analog_count > 0:
            analog_series = [[] for _ in range(analog_count)]
        for row in rows:
            d = row.get('digital')
            if d is None:
                for c in range(NUM_CHANNELS):
                    digital[c].append(0)
            else:
                for c in range(NUM_CHANNELS):
                    digital[c].append((d >> c) & 1)
            for i, val in enumerate(row.get('adc', [])):
                if i < analog_count:
                    analog_series[i].append(val)
        names = []
        ch_data = []
        # Digital channels
        has_digital = any(not (mode & 0x7) in (ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2, ANALOG_MODE_ANALOG4)
                         for _ in [1]) or (mode & ANALOG_ENABLE_BIT)
        pure_analog_only = mode in (ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2, ANALOG_MODE_ANALOG4)
        if not pure_analog_only or mode & ANALOG_ENABLE_BIT:
            ch_data.extend(digital)
            names.extend([f"CH{i}" for i in range(NUM_CHANNELS)])
        for i in range(analog_count):
            ch_data.append(analog_series[i])
            names.append(f"A{i}")
        if pure_analog_only and not analog_series:
            ch_data = digital
            names = [f"CH{i}" for i in range(NUM_CHANNELS)]
        self.ch_data = ch_data
        self.ch_names = names
        self.samplerate = rate
        self.captured_bytes = data
        self.wave.load(self.ch_data, self.ch_names, self.samplerate)
        self._fit_view()
        self.status['text'] = f"Captured {len(data)} bytes ({len(rows)} frames, mode {self.mode_cb.get()})"

    def _fit_view(self):
        w = self.wave.winfo_width()
        if self.capture_type.get() == 'rolling' and self.capture_window > 0:
            total = self.capture_window
        else:
            total = self.capture_nsamp if self.capture_nsamp > 0 else self.wave.num_samples
        if w > 10 and total > 0:
            self.wave.px_scale = (w - self.wave.LABEL_WIDTH) / total
            self.wave.scroll_x = 0
            self.wave.redraw()

    def _on_scroll(self, *args):
        pass

    def _update_scroll(self, *args):
        pass

    def _trig_mode_changed(self, event=None):
        mode = self.trig_mode.get()
        state = 'normal' if mode != 'Off' else 'disabled'
        for var in self.trig_ch_vars:
            var.set(False)
        # Find all channel checkbuttons in trig_frame's sub-frames and set state
        for child in self.trig_frame.winfo_children():
            if isinstance(child, ttk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, ttk.Checkbutton):
                        sub.configure(state=state)

    def _sync_ch_vis_ui(self):
        """Sync checkbox state in channel visibility section to wave state."""
        if not hasattr(self, 'ch_vis_vars') or not hasattr(self, 'ana_vis_vars'):
            return
        for i, var in enumerate(self.ch_vis_vars):
            if hasattr(self, 'wave') and i < len(self.wave.channel_visible):
                var.set(self.wave.channel_visible[i])
        for ai, var in enumerate(self.ana_vis_vars):
            idx = NUM_CHANNELS + ai
            if hasattr(self, 'wave') and idx < len(self.wave.channel_visible):
                var.set(self.wave.channel_visible[idx])

    def _gen_show_proto_fields(self, event=None):
        """Show/hide protocol-specific fields in the Generator tab."""
        proto = self.gen_proto.get()
        is_modbus = proto == 'Modbus'
        is_i2c = proto == 'I2C'
        is_uart = proto == 'UART'
        # Func code: show only for Modbus
        self.gen_func_lbl.grid() if is_modbus else self.gen_func_lbl.grid_remove()
        self.gen_func.grid() if is_modbus else self.gen_func.grid_remove()
        # Slave addr: show for I2C and Modbus
        self.gen_addr_lbl.grid() if (is_i2c or is_modbus) else self.gen_addr_lbl.grid_remove()
        self.gen_addr.grid() if (is_i2c or is_modbus) else self.gen_addr.grid_remove()
        # SCL Pin: show only for I2C
        self.gen_scl_lbl.grid() if is_i2c else self.gen_scl_lbl.grid_remove()
        self.gen_scl_pin.grid() if is_i2c else self.gen_scl_pin.grid_remove()
        # TX Pin label
        self.gen_tx_lbl.configure(text='TX Pin (SDA):' if is_i2c else 'TX Pin:')

    def _process_decoders(self):
        """Build filtered + decoded channels, arranged under source channels.
        
        Order: CH0, CH0_f (if filtered), CH0_decodes (if any), CH1, CH1_f, ...
        Decoder rows appear right below their source channel.
        Decoder functions always receive the full 16-channel base data.
        """
        ns = len(self.ch_data[0]) if self.ch_data else 0
        if ns == 0:
            return

        th = self.filter_threshold if hasattr(self, 'filter_threshold') else 3
        filt = self.filter_enabled if hasattr(self, 'filter_enabled') else [False] * NUM_CHANNELS
        slots = getattr(self, 'decoder_slots', [])
        base_data = self.ch_data[:NUM_CHANNELS]  # full 16-ch for decoder callbacks

        new_data = []
        new_names = []
        dec_text_lines = []

        for ci in range(NUM_CHANNELS):
            new_data.append(self.ch_data[ci])
            new_names.append(f"CH{ci}")

            if ci < len(filt) and filt[ci] and ci < len(self.ch_data):
                f = glitch_filter(self.ch_data[ci], th)
                new_data.append(f)
                new_names.append(f"CH{ci}_f")

            for slot in slots:
                if not slot.get('enabled', False):
                    continue
                src = slot['src_str']
                src_idx = slot['src_idx']
                src_matches = (src.isdigit() and src_idx == ci)
                if not src_matches and not src.isdigit():
                    src_matches = (new_names[-1] == src)
                if not src_matches:
                    continue

                sig_arr = [0] * ns
                frames = []
                th_slot = slot.get('thresh', 0)
                proto = slot.get('proto', 'UART')

                src_row = None
                for ri in range(len(new_data)):
                    if (src.isdigit() and ri == src_idx) or \
                       (not src.isdigit() and new_names[ri] == src):
                        src_row = ri
                        break
                if src_row is None:
                    continue
                chan_data = [new_data[src_row]]

                if proto == 'UART':
                    from_baud = slot.get('baud', 115200)
                    dec = decode_uart(chan_data, self.samplerate, 0, from_baud,
                                      filter_threshold=th_slot if th_slot > 0 else 0)
                    for r in dec:
                        frames.append({'type': 'byte', 'pos': r.pos, 'val': r.value,
                                       'end': r.pos + int(10 * self.samplerate / from_baud)})
                        for j in range(r.pos, min(r.pos + int(10 * self.samplerate / from_baud), ns)):
                            if j < len(sig_arr):
                                sig_arr[j] = 1
                elif proto == 'I2C':
                    sda_src = slot.get('sda_idx', 3)
                    scl_src = slot.get('scl_idx', 1)
                    dec = decode_i2c(base_data, self.samplerate, scl_src, sda_src,
                                     filter_threshold=th_slot if th_slot > 0 else 0)
                    for item in dec:
                        t, v = item
                        frames.append({'type': t, 'val': v})
                        if 0 < ns:
                            sig_arr[0] = 1
                elif proto == 'SPI':
                    spi_miso = slot.get('sda_idx', 3)
                    spi_sclk = slot.get('scl_idx', 1)
                    dec = decode_spi(base_data, self.samplerate, spi_miso, spi_sclk,
                                     filter_threshold=th_slot if th_slot > 0 else 0)
                    for bv in dec:
                        frames.append({'type': 'byte', 'val': bv})

                slot['frames'] = frames
                slot['sig'] = sig_arr
                new_data.append(sig_arr)
                new_names.append(f"{src}_{proto}")

                line = f"#{list(slots).index(slot)+1} {src} {proto}: "
                if proto == 'UART':
                    text = ''.join(chr(f['val']) if 32 <= f['val'] < 127 else f'[{f["val"]:02X}]'
                                  for f in frames[:50])
                    if len(frames) > 50:
                        text += '...'
                    line += f'"{text}"  ({len(frames)} bytes)'
                elif proto == 'I2C':
                    parts = []
                    for f in frames[:30]:
                        if f['type'] == 'START':
                            parts.append('S')
                        elif f['type'] == 'STOP':
                            parts.append('P')
                        elif f['val'] is not None:
                            parts.append(f"0x{f['val']:02X}")
                    line += ' '.join(parts)
                elif proto == 'SPI':
                    line += ' '.join(f"0x{f['val']:02X}" for f in frames[:30])
                dec_text_lines.append(line)

        self.ch_data = new_data
        self.ch_names = new_names

        if hasattr(self, 'wave') and hasattr(self.wave, 'channel_visible'):
            while len(self.wave.channel_visible) < len(new_data):
                self.wave.channel_visible.append(True)

        if hasattr(self, 'dec_out'):
            self.dec_out.delete('1.0', 'end')
            self.dec_out.insert('1.0', '\n'.join(dec_text_lines) if dec_text_lines else "No decoders active")

    def _gen_send(self):
        if not self.dev: return
        proto = self.gen_proto.get()
        data_s = self.gen_data.get('1.0', 'end-1c')
        if not data_s: return
        is_rolling = self.capture_type.get() == 'rolling'
        # If rolling, queue gen params — rolling thread loads + starts gen (no serial access)
        if is_rolling:
            try:
                tx_pin = int(self.gen_tx_pin.get())
            except: tx_pin = 3
            self.dev._pending_gen = {
                'data': data_s.encode(),
                'baud': int(self.gen_baud.get()),
                'tx_pin': tx_pin,
                'proto': proto
            }
            self.status['text'] = "Generator queued — appears in rolling window"
            return
        self.status['text'] = f"Sending {len(data_s)} bytes..."
        try:
            tx_pin = int(self.gen_tx_pin.get())
            scl_pin = int(self.gen_scl_pin.get())
            if proto == 'UART':
                self.dev.send_uart(data_s.encode(), int(self.gen_baud.get()),
                                   tx_pin=tx_pin)
            elif proto == 'I2C':
                self.dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
                addr = int(self.gen_addr.get(), 16)
                self.dev._long(CMD_GEN_PROTO, 1)
                div = max(1, self.dev.sys_clk // int(self.gen_baud.get()) // 2)
                self.dev._long(CMD_GEN_BAUD, div & 0xFFFF)
                frame = bytes([(addr << 1) & 0xFF]) + data_s.encode()
                self.dev._load_block(frame)
                self.dev.start_gen()
            # If rolling, queue gen params — rolling thread loads + starts gen
            if is_rolling:
                self.dev._pending_gen = {
                    'data': data_s.encode(),
                    'baud': int(self.gen_baud.get()),
                    'tx_pin': tx_pin,
                    'proto': proto
                }
                self.status['text'] = "Generator started"
        except Exception as e:
            self.status['text'] = f"Gen error: {e}"

    def _gen_send_capture(self):
        """Atomic generator capture via CMD_GEN_CAPTURE hardware FSM."""
        if not self.dev: return
        proto = self.gen_proto.get()
        data_s = self.gen_data.get('1.0', 'end-1c')
        if not data_s: return
        if self.capture_type.get() == 'rolling':
            try:
                tx_pin = int(self.gen_tx_pin.get())
            except: tx_pin = 3
            self.dev._pending_gen = {
                'data': data_s.encode(),
                'baud': int(self.gen_baud.get()),
                'tx_pin': tx_pin,
                'proto': proto
            }
            self.status['text'] = "Generator queued — appears in rolling window"
            return

        rate_str = self.rate_cb.get()
        rate = int(rate_str.replace('kHz','000').replace('MHz','000000'))
        nsamp = self._get_samples()
        self.status['text'] = f"Capturing with generator {nsamp} @ {rate/1e6:.1f} MHz..."
        print(f"[DBG] gen_capture rate={rate} nsamp={nsamp}")
        self.win.update()

        try:
            tx_pin = int(self.gen_tx_pin.get())
            scl_pin = int(self.gen_scl_pin.get())
            if proto == 'I2C':
                addr = int(self.gen_addr.get(), 16)
                i2c_frame = bytes([(addr << 1) & 0xFF]) + data_s.encode()
                data = self.dev.capture_with_gen(
                    rate_hz=rate, nsamples=nsamp, timeout=6,
                    proto='I2C',
                    i2c_speed=int(self.gen_baud.get()),
                    i2c_frame=i2c_frame,
                    i2c_tx_pin=tx_pin,
                    i2c_scl_pin=scl_pin,
                )
            else:
                # UART / Modbus: use _gen_data to pass to capture_with_gen
                self.dev._gen_data = data_s.encode()
                self.dev._gen_baud = int(self.gen_baud.get())
                self.dev._gen_tx_pin = tx_pin
                data = self.dev.capture_with_gen(rate_hz=rate, nsamples=nsamp, timeout=6)
        except Exception as e:
            print(f"[DBG] gen_capture EXCEPTION: {e}")
            self.status['text'] = f"Capture error: {e}"
            return

        print(f"[DBG] gen_capture returned {len(data)} bytes")
        if not data:
            self.status['text'] = "Capture returned 0 bytes"
            return

        ch_data, ns = samples_to_channels(data)
        if ns == 0 or not ch_data or not ch_data[0]:
            self.status['text'] = "No samples decoded from capture"
            return
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self.wave.load(ch_data, self.ch_names, self.samplerate)
        ch_tx = ch_data[tx_pin] if 0 <= tx_pin < len(ch_data) else ch_data[0]
        trans = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i-1])
        self.status['text'] = f"Captured {ns} samples ({trans} trans on CH{tx_pin})"
        self.wave.highlight_channel(tx_pin)
        self._process_decoders()
        self.wave.redraw()

    def _accel_read(self, reg_addr, read_len):
        """Read LIS3DH register(s) via I2C and display result."""
        if not self.dev:
            self.status['text'] = "No device"
            return
        try:
            addr = int(self.acc_addr.get(), 16)
            speed = int(self.acc_speed.get())
            sda_pin = int(self.acc_sda_pin.get())
            scl_pin = int(self.acc_scl_pin.get())
            rate = 4_000_000
            nsamp = max(50000, read_len * 200)
            self.status['text'] = f"Reading accel reg 0x{reg_addr:02X}..."
            self.win.update()
            data = self.dev.i2c_capture_with_gen(
                rate_hz=rate, nsamples=nsamp, i2c_speed=speed,
                dev_addr=addr, reg_addr=reg_addr,
                read_len=read_len,
                tx_pin=sda_pin, scl_pin=scl_pin, fast_mode=False)
            if not data:
                self._show_accel_result("No data returned")
                return
            ch, ns = samples_to_channels(data)
            self.ch_data = ch
            self.samplerate = rate
            self.captured_bytes = data
            self.decoded_uart = []
            self.decoded_i2c = []
            self.wave.load(ch, self.ch_names, self.samplerate)
            self._process_decoders()
            self.wave.redraw()
            # Parse I2C decoded bytes
            decoded = decode_i2c(ch, rate, scl_idx=scl_pin, sda_idx=sda_pin)
            data_bytes = [v for t, v in decoded if t == "DATA"]
            if reg_addr in (0x28, 0x2A, 0x2C) and read_len >= 2 and len(data_bytes) >= 2:
                raw = (data_bytes[-2] | (data_bytes[-1] << 8))
                val = raw - 65536 if raw >= 32768 else raw
                mg = val * 1000 // 16384
                label = {0x28: "X", 0x2A: "Y", 0x2C: "Z"}.get(reg_addr, "?")
                self._show_accel_result(f"{label} axis: {raw:04X} ({val: d}) = {mg} mg")
            elif reg_addr == 0x0F and data_bytes:
                who = data_bytes[-1]
                ok = "✓ LIS3DH" if who == 0x33 else f"✗ 0x{who:02X} (expected 0x33)"
                self._show_accel_result(f"WHO_AM_I = 0x{who:02X}  {ok}")
            elif data_bytes:
                pairs = [f"0x{v:02X}" for v in data_bytes[-read_len:]]
                self._show_accel_result(f"Data: {' '.join(pairs)}")
            else:
                self._show_accel_result("No I2C data decoded")
            if ns > 0:
                self.status['text'] = f"Accel: {ns} samples"
        except Exception as e:
            self._show_accel_result(f"Error: {e}")

    def _show_accel_result(self, text):
        self.acc_result.config(state='normal')
        self.acc_result.delete('1.0', 'end')
        self.acc_result.insert('1.0', text)
        self.acc_result.config(state='disabled')

    def _export_ols(self):
        if not self.captured_bytes:
            messagebox.showinfo("Export", "No data to export")
            return
        fname = filedialog.asksaveasfilename(defaultextension='.ols',
                                              filetypes=[('OLS files', '*.ols'), ('All', '*.*')])
        if not fname: return
        rate = self.samplerate
        mode = self.capture_mode
        has_analog = mode != ANALOG_MODE_DIGITAL8
        is_analog8 = bool(mode & ANALOG_ENABLE_BIT)
        stride = self.capture_stride

        with open(fname, 'w') as f:
            f.write(f';Rate: {rate}\n')
            f.write(';Channels: 16\n')
            f.write(';EnabledChannels: -1\n')
            if has_analog:
                # Decode analog frames
                frames = decode_analog_frames(self.captured_bytes, mode)
                ana_count = 8 if is_analog8 else analog_frame_stride(mode) - 2
                f.write(f';Analog: {ana_count}\n')
                for i, fr in enumerate(frames):
                    d = fr.get('digital', 0)
                    f.write(f'{d:04x}@{i}\n')
                    for ai, av in enumerate(fr.get('adc', [])):
                        f.write(f';A{ai}: {av}@{i}\n')
            else:
                stride = getattr(self, 'capture_stride', 4)
                ch_data, ns = samples_to_channels(self.captured_bytes, stride=stride)
                for i in range(ns):
                    byte = 0
                    for c in range(NUM_CHANNELS):
                        if c < len(ch_data) and i < len(ch_data[c]):
                            byte |= (ch_data[c][i] << c)
                    f.write(f'{byte:04x}@{i}\n')
        self.status['text'] = f"Saved {fname}"

    def _export_sr(self):
        if not self.captured_bytes:
            return
        import zipfile, io
        fname = filedialog.asksaveasfilename(defaultextension='.sr',
                                              filetypes=[('Sigrok', '*.sr'), ('All', '*.*')])
        if not fname: return
        rate = self.samplerate
        mode = self.capture_mode
        has_analog = mode != ANALOG_MODE_DIGITAL8
        stride = self.capture_stride

        if has_analog:
            frames = decode_analog_frames(self.captured_bytes, mode)
            ana_count = len(frames[0].get('adc', [])) if frames else 0
            meta = f"""[global]
sigrok version=OLSMScope 1.0

[device 1]
capturefile=logic
total probes=16
samplerate={rate} Hz
total analog={ana_count}
probe1=0
probe2=1
probe3=2
probe4=3
probe5=4
probe6=5
probe7=6
probe8=7
unitsize=1
"""
            with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('metadata', meta)
                # Digital logic data (first 2 bytes per frame)
                logic = b''
                for fr in frames:
                    d = fr.get('digital', 0)
                    logic += bytes([d & 0xFF, (d >> 8) & 0xFF])
                zf.writestr('logic-1', logic)
                # Analog probe files (32-bit float LE)
                for ai in range(ana_count):
                    import struct
                    a_bytes = b''
                    for fr in frames:
                        vals = fr.get('adc', [])
                        v = vals[ai] / 4095.0 * 3.3 if ai < len(vals) else 0.0
                        a_bytes += struct.pack('<f', v)
                    zf.writestr(f'analog-{ai+1}', a_bytes)
        else:
            meta = f"""[global]
sigrok version=OLSMScope 1.0

[device 1]
capturefile=logic
total probes=16
samplerate={rate} Hz
total analog=0
probe1=0
probe2=1
probe3=2
probe4=3
probe5=4
probe6=5
probe7=6
probe8=7
unitsize=1
"""
            with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('metadata', meta)
                logic = bytes([self.captured_bytes[i * stride] for i in range(len(self.captured_bytes)//stride)])
                zf.writestr('logic-1', logic)
        self.status['text'] = f"Saved {fname}"

    def _export_clip(self):
        if not self.captured_bytes:
            return
        mode = self.capture_mode
        has_analog = mode != ANALOG_MODE_DIGITAL8
        lines = []
        if has_analog:
            frames = decode_analog_frames(self.captured_bytes, mode)
            lines.append(f"Samplerate: {self.samplerate} Hz, Frames: {len(frames)}")
            if frames:
                d0 = frames[0].get('digital', 0)
                lines.append(f"First digital word: 0x{d0:04X}")
                ana_count = len(frames[0].get('adc', []))
                for ai in range(min(ana_count, 8)):
                    vals = [fr.get('adc', [0])[ai] for fr in frames[:100]]
                    if vals:
                        lines.append(f"A{ai}: min={min(vals)} max={max(vals)} avg={sum(vals)//len(vals)}")
        else:
            stride = getattr(self, 'capture_stride', 4)
            ch_data, ns = samples_to_channels(self.captured_bytes, stride=stride)
            lines.append(f"Samplerate: {self.samplerate} Hz, Samples: {ns}")
            ch0 = ''.join(str(ch_data[0][i]) for i in range(min(200, ns)))
            lines.append(f"CH0[0:{min(200,ns)}]: {ch0}")
            if self.decoded_uart:
                lines.append(f"\nUART ({len(self.decoded_uart)} bytes):")
                lines.extend(f"  0x{r.value:02X} '{chr(r.value) if 32<=r.value<127 else '.'}'"
                            for r in self.decoded_uart[:30])
        self.win.clipboard_clear()
        self.win.clipboard_append('\n'.join(lines))
        self.status['text'] = "Copied to clipboard"

    def _export_marker_range(self):
        """Export sample range between markers M1 and M2 as .ols."""
        m1 = self.wave.marker1
        m2 = self.wave.marker2
        if m1 is None or m2 is None:
            messagebox.showinfo("Export Range", "Set markers M1 and M2 first\n(click on waveform to place markers)")
            return
        if m1 > m2:
            m1, m2 = m2, m1
        cb = self.captured_bytes
        if not cb:
            messagebox.showinfo("Export Range", "No captured data to export")
            return
        stride = getattr(self, 'capture_stride', 4)
        start_b = m1 * stride
        end_b = min((m2 + 1) * stride, len(cb))
        trimmed = cb[start_b:end_b]
        if len(trimmed) < stride:
            messagebox.showinfo("Export Range", "Range too small (need >= 1 sample)")
            return
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(defaultextension='.ols',
                                              filetypes=[('OLS files', '*.ols'), ('All', '*.*')])
        if not fname:
            return
        ch, ns = samples_to_channels(trimmed, stride=stride)
        rate = self.samplerate
        with open(fname, 'w') as f:
            f.write(f';Rate: {rate}\n;Channels: 16\n;EnabledChannels: -1\n;Range: M1={m1} M2={m2}\n')
            for i in range(ns):
                byte = 0
                for c in range(NUM_CHANNELS):
                    if c < len(ch) and i < len(ch[c]):
                        byte |= (ch[c][i] << c)
                f.write(f'{byte:04x}@{i}\n')
        self.status['text'] = f"Exported range M1={m1}..M2={m2} ({ns} samples) to {fname}"

    def _update_export_size_label(self):
        """Update the export tab's captured data size estimate."""
        cb = self.captured_bytes
        if cb:
            stride = max(1, getattr(self, 'capture_stride', 4))
            ns = len(cb) // stride
            mb = len(cb) / (1024 * 1024)
            self.export_size_lbl['text'] = f"Captured: {ns} samples ({mb:.1f} MB)"
        else:
            self.export_size_lbl['text'] = "Captured: 0 samples (0 MB)"

    # --- Data Logger ---

    def _log_browse(self):
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if fname:
            self.log_csv_path_v.set(fname)

    def _logger_arm(self):
        if not self.dev:
            self.log_status['text'] = "Not connected"; return
        self.logger_running = True
        self.logger_count = 0
        self.logger_stop_evt.clear()
        self.logger_csv_path = self.log_csv_path_v.get() or 'data_log.csv'
        self.log_arm_btn.configure(state='disabled')
        self.log_stop_btn.configure(state='normal')
        self.log_count_label['text'] = "Captures: 0"
        self.log_out.delete('1.0', 'end')
        self.log_status['text'] = "Logger armed..."
        rate_str = self.log_rate.get()
        self.logger_rate_hz = int(rate_str.replace('kHz','000').replace('MHz','000000'))
        try: ns = int(self.log_nsamp.get())
        except: ns = 1024
        self.logger_nsamp = max(64, min(ns, 500000))
        hdr = 'timestamp,trigger_num,nsamples,ch0_edges,ch1_edges,ch2_edges,ch3_edges,ch4_edges,ch5_edges,ch6_edges,ch7_edges,raw_hex\n'
        try:
            with open(self.logger_csv_path, 'w') as f: f.write(hdr)
        except Exception as e:
            self.log_status['text'] = f"CSV error: {e}"; return
        t = threading.Thread(target=self._logger_thread, daemon=True)
        t.start()

    def _logger_stop(self):
        self.logger_stop_evt.set()
        self.logger_running = False
        self.log_arm_btn.configure(state='normal')
        self.log_stop_btn.configure(state='disabled')
        self.log_status['text'] = f"Stopped - {self.logger_count} captures"

    def _build_trigger_from_logger_ui(self):
        m = self.log_trig_mode.get()
        return None if m == 'Off' else ('rising' if m == 'Rising' else ('falling' if m == 'Falling' else None))

    def _append_csv_row(self, row):
        if not self.logger_csv_path: return
        try:
            with open(self.logger_csv_path, 'a') as f: f.write(','.join(str(v) for v in row) + '\n')
        except Exception as e:
            self.log_status['text'] = f"CSV append error: {e}"

    def _logger_thread(self):
        dev = self.dev; stop = self.logger_stop_evt
        rate = self.logger_rate_hz; nsamp = self.logger_nsamp
        trig = self._build_trigger_from_logger_ui()
        mode = self.log_trig_mode.get()
        timeout = max(10, nsamp // 5000 + 5)
        while not stop.is_set():
            if mode == 'Protocol':
                try: mb = int(self.proto_match.get(), 16) & 0xFF
                except: mb = 0x57
                try: pc = int(self.proto_ch.get())
                except: pc = 0
                try: pb = int(self.proto_baud.get())
                except: pb = 115200
                dev.trigger_decode(match_byte=mb, channel=pc, baud=pb, enable=True)
            elif mode == 'Off':
                dev.trigger_decode(match_byte=0x00, enable=False)
            try:
                data = dev.capture(rate_hz=rate, nsamples=nsamp, timeout=timeout, trigger=trig, stop_evt=stop)
            except Exception as e:
                if not stop.is_set():
                    self.win.after(0, lambda e=e: self.log_status.configure(text=f"Error: {e}"))
                break
            if stop.is_set() or not data or len(data) < 4:
                continue
            self.logger_count += 1
            c = self.logger_count
            chd, ns = samples_to_channels(data)
            edges = [sum(1 for i in range(1, min(ns, len(chd[ci]))) if chd[ci][i] != chd[ci][i-1]) for ci in range(min(len(chd), NUM_CHANNELS))]
            while len(edges) < NUM_CHANNELS:
                edges.append(0)
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            row = [ts, c, ns] + edges + [data.hex()[:120]]
            self._append_csv_row(row)
            self.win.after(0, self._log_update_ui, data, c)

    def _log_update_ui(self, data, count):
        self.log_count_label['text'] = f"Captures: {count}"
        self.log_status['text'] = f"Capture #{count} - {len(data)} bytes"
        chd, ns = samples_to_channels(data)
        self.wave.ch_data = chd
        self.wave.num_samples = ns
        self.wave._drawn_to = 0
        w = max(100, self.wave.winfo_width() - self.wave.LABEL_WIDTH)
        self.wave.px_scale = w / max(1, ns)
        self.wave.scroll_x = 0
        self.wave.delete('all')
        self.wave.redraw()
        self.log_out.insert('end', f"#{count}: {ns} samples\n")
        self.log_out.see('end')

    def run(self):
        if HAS_TK:
            self.win.mainloop()

# ─── CLI Mode ──────────────────────────────────────────────────

def cli_mode(args):
    """Command-line interface for automated capture and testing (SPI only)."""
    if args.command == 'decode' and args.input:
        dev = None
    else:
        if not HAS_SPI:
            print("ERROR: SPI backend unavailable (ftd2xx required)")
            return 1
        dev = OLSDeviceSPI()
        try:
            dev.open()
            print("Connected via SPI @ 30 MHz")
        except Exception as e:
            print(f"ERROR: Cannot open SPI device: {e}")
            return 1

    if args.command == 'capture':
        data = dev.capture(rate_hz=args.rate, nsamples=args.samples, timeout=args.timeout or 5)
        ch_data, ns = samples_to_channels(data)
        print(f"Captured {len(data)} bytes ({ns} samples)")
        if args.output:
            with open(args.output, 'wb') as f:
                f.write(data)
            print(f"Saved raw to {args.output}")
        if ns == 0 or not ch_data or not ch_data[0]:
            print("No samples decoded from capture")
            return 1
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        print(f"CH0: {sum(ch0)}H/{len(ch0)-sum(ch0)}L, {trans} transitions")

    elif args.command == 'decode':
        with open(args.input, 'rb') as f:
            data = f.read()
        if args.format == 'raw4':
            # Raw 4-byte samples from capture
            pass
        elif args.format == 'sr':
            import zipfile
            with zipfile.ZipFile(args.input) as zf:
                logic_files = [n for n in zf.namelist() if n.startswith('logic')]
                if not logic_files:
                    print("Error: no 'logic' file in SR archive")
                    return 1
                data = zf.read(logic_files[0])
        ch_data, ns = samples_to_channels(data)
        rate = args.rate or 1_000_000
        if args.protocol == 'uart':
            res = decode_uart(ch_data, rate, args.channel or 0, args.baud or 115200)
            for r in res:
                print(f"0x{r.value:02X}  '{chr(r.value) if 32<=r.value<127 else '.'}'")
            print(f"Total: {len(res)} bytes")
        elif args.protocol == 'i2c':
            sda_ch = getattr(args, 'sda_ch', 2)
            scl_ch = getattr(args, 'scl_ch', 3)
            res = decode_i2c(ch_data, rate, scl_ch, sda_ch)
            for t, v in res:
                if v is not None:
                    print(f"{t} 0x{v:02X}")
                else:
                    print(t)
        elif args.protocol == 'modbus':
            res = decode_modbus(ch_data, rate, args.channel or 0, args.baud or 115200)
            for f in res:
                data_hex = ' '.join(f'{b:02X}' for b in f.data)
                crc_str = "OK" if f.crc_ok else "BAD"
                print(f"Addr=0x{f.addr:02X} Func=0x{f.func:02X} Data=[{data_hex}] CRC=0x{f.crc:04X} {crc_str}")
            print(f"Total: {len(res)} frames")

    elif args.command == 'send':
        if args.data:
            data = args.data.encode()
        elif args.input:
            with open(args.input, 'rb') as f:
                data = f.read()
        else:
            print("Provide --data or --input")
            return 1
        tx_pin = getattr(args, 'tx_pin', 3)
        scl_pin = getattr(args, 'scl_pin', 1)
        if args.protocol == 'i2c':
            addr = int(args.addr, 16) if args.addr else 0x28
            dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
            frame = bytes([(addr << 1) & 0xFF]) + data
            dev._long(CMD_GEN_PROTO, 1)
            div = max(1, dev.sys_clk // (args.baud or 100000) // 2)
            dev._long(CMD_GEN_BAUD, div & 0xFFFF)
            dev._load_block(frame)
        elif args.protocol == 'modbus':
            slave = int(args.addr, 16) if args.addr else 0x01
            func = int(getattr(args, 'func', '0x03'), 16)
            frame = bytes([slave, func]) + data
            frame += struct.pack('<H', modbus_crc16(frame))
            dev.send_uart(frame, baud=args.baud or 9600, tx_pin=tx_pin)
        else:
            dev.send_uart(data, baud=args.baud or 115200,
                          tx_pin=tx_pin)
        dev.start_gen()
        print(f"Sent {len(data)} bytes ({args.protocol or 'UART'})")

        if args.capture:
            cap = dev.capture_with_gen(rate_hz=args.rate or 1_000_000, nsamples=args.samples or 5000)
            print(f"Captured {len(cap)} bytes")

    if dev:
        dev.close()
    return 0

def splash_choose():
    """Auto-detect SPI device. Returns 'SPI' or None."""
    if HAS_SPI and find_spi_device():
        return 'SPI'
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.update_idletasks()
    ww = win.winfo_width(); wh = win.winfo_height()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    win.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")
    win.focus_force()
    win.grab_set()
    win.wait_window()
    return result[0]


def main():
    if '--cli' in sys.argv or len(sys.argv) > 1 and sys.argv[1] in ('capture','decode','send','--help','-h','--version'):
        # Filter out --cli so argparse doesn't choke on it
        argv = [a for a in sys.argv if a != '--cli']
        p = argparse.ArgumentParser(description='OLS MaxScope CLI')
        p.add_argument('--version', action='version', version=f'OLS MaxScope {__version__}')
        p.add_argument('command', nargs='?', default='capture',
                      choices=['capture','decode','send'])
        p.add_argument('--port', default=None)
        p.add_argument('--rate', type=int, default=1_000_000)
        p.add_argument('--samples', type=int, default=5000)
        p.add_argument('--timeout', type=int, default=None)
        p.add_argument('--output', default=None)
        p.add_argument('--input', default=None)
        p.add_argument('--protocol', default='uart', choices=['uart','i2c','modbus'])
        p.add_argument('--baud', type=int, default=115200)
        p.add_argument('--channel', type=int, default=0)
        p.add_argument('--addr', default='0x28')
        p.add_argument('--data', default=None)
        p.add_argument('--capture', action='store_true')
        p.add_argument('--func', default='0x03')
        p.add_argument('--tx-pin', type=int, default=3)
        p.add_argument('--scl-pin', type=int, default=1)
        p.add_argument('--backend', default='UART', choices=['UART','SPI'])
        args = p.parse_args(argv[1:])
        if args.backend == 'SPI' and HAS_SPI and args.command == 'capture':
            # SPI CLI capture
            dev = OLSDeviceSPI()
            dev.open()
            data = dev.capture(rate_hz=args.rate, nsamples=args.samples, timeout=args.timeout or 5)
            ch_data, ns = samples_to_channels(data)
            print(f"Captured {len(data)} bytes ({ns} samples) via SPI")
            if args.output:
                with open(args.output, 'wb') as f: f.write(data)
            if ns == 0 or not ch_data or not ch_data[0]:
                print("No samples decoded from capture")
                dev.close()
                sys.exit(1)
            ch0 = ch_data[0]
            trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
            print(f"CH0: {sum(ch0)}H/{len(ch0)-sum(ch0)}L, {trans} transitions")
            dev.close()
            sys.exit(0)
        sys.exit(cli_mode(args))
    else:
        backend = splash_choose()
        if backend is None:
            backend = 'UART'
            print("No OLS device detected — opening in disconnected state")
        root = tk.Tk()
        root.withdraw()
        app = OLScope(backend=backend, root=root)
        root.deiconify()
        app.win.after(100, app._auto_connect)
        app.run()

if __name__ == '__main__':
    main()
