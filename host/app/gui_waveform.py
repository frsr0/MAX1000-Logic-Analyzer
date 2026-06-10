"""
Scrollable/zoomable digital waveform viewer with markers and measurement.
"""
import tkinter as tk


class WaveformDisplay(tk.Canvas):
    CH_HEIGHT = 30
    CH_GAP = 4
    LABEL_WIDTH = 40
    MIN_PX_PER_SAMPLE = 0.5
    MAX_PX_PER_SAMPLE = 50
    RULER_H = 20
    DECODE_H = 20

    def __init__(self, parent, app=None, **kw):
        super().__init__(parent, bg='white', **kw)
        self.app = app
        self.ch_data = []
        self.ch_names = []
        self.samplerate = 1_000_000
        self.num_samples = 0
        self.px_scale = 2.0
        self.scroll_x = 0
        self.marker1 = None
        self.marker2 = None
        self.dragging = None
        self._drawn_to = 0
        self.channel_visible = []
        self._bind_events()

    def _bind_events(self):
        self.bind('<MouseWheel>', self._on_wheel)
        self.bind('<ButtonPress-1>', self._on_click)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Configure>', lambda e: self.redraw())

    def _calc_ch_height(self):
        n = sum(1 for v in self.channel_visible if v)
        if n == 0:
            return self.CH_HEIGHT
        h = self.winfo_height()
        available = h - self.RULER_H - self.DECODE_H
        return max(15, available // n)

    def _visible_indices(self):
        return [i for i, v in enumerate(self.channel_visible) if v]

    def set_channel_visible(self, idx, visible):
        if idx < len(self.channel_visible):
            self.channel_visible[idx] = visible
            self.redraw()

    def toggle_channel(self, idx):
        if idx < len(self.channel_visible):
            self.channel_visible[idx] = not self.channel_visible[idx]
            self.redraw()

    def load(self, ch_data, ch_names, samplerate):
        self.ch_data = ch_data
        self.ch_names = ch_names
        self.samplerate = samplerate
        self.num_samples = len(ch_data[0]) if ch_data else 0
        self.channel_visible = [True] * len(ch_data)
        self.marker1 = None
        self.marker2 = None
        self.scroll_x = 0
        self._drawn_to = 0
        self.redraw()

    def draw_incremental(self, upto):
        if upto <= self._drawn_to or not self.ch_data:
            return
        self.delete('live')
        w = self.winfo_width()
        ruler_h = self.RULER_H
        start = max(0, self._drawn_to)
        end = min(self.num_samples, upto)
        ch_h = self._calc_ch_height()
        vis = self._visible_indices()
        for vi, ci in enumerate(vis):
            y0 = ruler_h + vi * (ch_h + self.CH_GAP)
            samples = self.ch_data[ci]
            is_analog = samples and max(samples) > 1
            points = []
            prev = samples[start] if start > 0 else samples[0]
            for si in range(start, end):
                v = samples[si]
                px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                if is_analog:
                    py = y0 + ch_h - (float(v) / 4095.0) * ch_h
                else:
                    py = y0 + (0 if v else ch_h)
                if si > start and v != prev:
                    lpx = self.LABEL_WIDTH + (si - 1 - self.scroll_x) * self.px_scale
                    points.extend([lpx, py, px, py])
                points.extend([px, py])
                prev = v
            if points:
                self.create_line(points, fill='#0066cc', width=1.3, tags='live')
        self._drawn_to = upto

    def set_scale(self, px_scale):
        old = self.px_scale
        self.px_scale = max(self.MIN_PX_PER_SAMPLE, min(self.MAX_PX_PER_SAMPLE, px_scale))
        w = self.winfo_width()
        center = self.scroll_x + w / 2 / old if old else 0
        self.scroll_x = max(0, center - w / 2 / self.px_scale)
        self.redraw()

    def _on_wheel(self, e):
        delta = 1.2 if e.delta > 0 else 0.8
        self.set_scale(self.px_scale * delta)
        return "break"

    def _on_click(self, e):
        if e.x < self.LABEL_WIDTH:
            ruler_h = self.RULER_H
            ch_h = self._calc_ch_height()
            vis = self._visible_indices()
            for vi, ci in enumerate(vis):
                y0 = ruler_h + vi * (ch_h + self.CH_GAP)
                if y0 <= e.y < y0 + ch_h:
                    self.toggle_channel(ci)
                    if self.app:
                        self.app._sync_ch_vis_ui()
                    return
            return
        sx = self.scroll_x + (e.x - self.LABEL_WIDTH) / self.px_scale
        if 0 <= sx < self.num_samples:
            si = int(sx)
            if self.marker1 is None:
                self.marker1 = si
            elif self.marker2 is None:
                self.marker2 = si
                if self.marker1 > self.marker2:
                    self.marker1, self.marker2 = self.marker2, self.marker1
            else:
                self.marker1 = si
                self.marker2 = None
            self.dragging = 'marker2' if self.marker2 is not None else 'marker1'
            self.redraw()

    def _on_drag(self, e):
        if self.dragging and e.x >= self.LABEL_WIDTH:
            sx = self.scroll_x + (e.x - self.LABEL_WIDTH) / self.px_scale
            si = max(0, min(self.num_samples - 1, int(sx)))
            if self.dragging == 'marker1':
                self.marker1 = si
            else:
                self.marker2 = si
            if self.marker1 is not None and self.marker2 is not None:
                if self.marker1 > self.marker2:
                    self.marker1, self.marker2 = self.marker2, self.marker1
                    self.dragging = 'marker1' if self.dragging == 'marker2' else 'marker2'
            self.redraw()

    def _on_release(self, e):
        self.dragging = None

    def highlight_channel(self, ch_idx):
        self.redraw()
        ruler_h = self.RULER_H
        ch_h = self._calc_ch_height()
        vis = self._visible_indices()
        vi = 0
        for i, ci in enumerate(vis):
            if ci == ch_idx:
                vi = i
                break
        y0 = ruler_h + vi * (ch_h + self.CH_GAP)
        cw = self.winfo_width()
        self.create_rectangle(self.LABEL_WIDTH - 4, y0 - 1, cw, y0 + ch_h + 1,
                              outline='#ff0', fill='', width=2, tags='highlight')

    def total_height(self):
        n = sum(1 for v in self.channel_visible if v)
        return n * (self._calc_ch_height() + self.CH_GAP) + self.RULER_H + self.DECODE_H

    def redraw(self):
        self.delete('all')
        w = self.winfo_width()
        if w < 10:
            return
        nch = len(self.ch_data)
        if nch == 0 or self.num_samples == 0:
            return

        ruler_h = self.RULER_H
        ch_h = self._calc_ch_height()
        vis = self._visible_indices()

        self.create_rectangle(0, 0, w, ruler_h, fill='#eee', outline='')
        if self.px_scale > 0 and self.samplerate > 0:
            px_per_div = 100
            for step_ns in [1, 2, 5, 10, 20, 50, 100, 200, 500,
                            1000, 2000, 5000, 10000, 20000, 50000,
                            100000, 200000, 500000, 1000000]:
                step_samp = step_ns * self.samplerate / 1e9
                if step_samp * self.px_scale >= 50:
                    break
            start_samp = int(self.scroll_x / step_samp) * step_samp
            t = start_samp
            while True:
                px = self.LABEL_WIDTH + (t - self.scroll_x) * self.px_scale
                if px > w:
                    break
                if px >= self.LABEL_WIDTH:
                    self.create_line(px, 0, px, ruler_h // 2, fill='#666')
                    if step_ns < 1000:
                        label = f"{t * 1e9 / self.samplerate:.0f} ns"
                    elif step_ns < 1000000:
                        label = f"{t * 1e9 / self.samplerate / 1000:.1f} µs"
                    else:
                        label = f"{t / self.samplerate * 1000:.1f} ms"
                    self.create_text(px + 2, ruler_h // 2 + 2, text=label, anchor='w',
                                    font=('Consolas', 7), fill='#333')
                t += step_samp

        for vi, ci in enumerate(vis):
            y0 = ruler_h + vi * (ch_h + self.CH_GAP)
            name = self.ch_names[ci] if ci < len(self.ch_names) else f"D{ci}"
            is_dec = any(name.endswith(f'_{p}') for p in ['UART', 'I2C', 'SPI'])
            is_filt = name.endswith('_f')

            clr = '#2a7' if is_dec else '#069' if is_filt else '#000'
            self.create_text(2, y0 + ch_h / 2, text=name, anchor='w',
                            font=('Consolas', 9), fill=clr)

            samples = self.ch_data[ci]
            is_analog = samples and max(samples) > 1
            start = max(0, int(self.scroll_x))
            end = min(len(samples), int(self.scroll_x + w / self.px_scale) + 1)

            if is_dec:
                mid_y = y0 + ch_h / 2
                self.create_line(self.LABEL_WIDTH, mid_y, w, mid_y, fill='#ccc', width=0.5)
                spb_samp = 0
                if '_UART' in name:
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'):
                            continue
                        dname = f"{slot['src_str']}_UART"
                        if dname == name:
                            spb_samp = self.samplerate / slot.get('baud', 115200)
                            for f in slot.get('frames', []):
                                if f['type'] != 'byte':
                                    continue
                                px = self.LABEL_WIDTH + (f['pos'] - self.scroll_x) * self.px_scale
                                fw = 10 * spb_samp * self.px_scale
                                if px + fw < self.LABEL_WIDTH or px > w:
                                    continue
                                self.create_rectangle(px, y0, px + fw, y0 + ch_h,
                                                     outline='#2a7', width=0.5, fill='#e8ffe8')
                                txt = chr(f['val']) if 32 <= f['val'] < 127 else f'[{f["val"]:02X}]'
                                self.create_text(px + 2, y0 + 2, text=txt, anchor='nw',
                                                font=('Consolas', 6), fill='#2a7')
                elif '_SPI' in name:
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'):
                            continue
                        dname = f"{slot['src_str']}_SPI"
                        if dname == name:
                            for f in slot.get('frames', []):
                                pass
                elif '_I2C' in name:
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'):
                            continue
                        dname = f"{slot['src_str']}_I2C"
                        if dname == name:
                            for f in slot.get('frames', []):
                                if f['type'] == 'START':
                                    self.create_text(self.LABEL_WIDTH + 4, mid_y, text='S',
                                                    font=('Consolas', 8), fill='#a72')
                                elif f['type'] == 'STOP':
                                    self.create_text(px - 8, y0 + 2, text='P',
                                                    font=('Consolas', 8), fill='#a72')
            else:
                if start >= end:
                    continue
                points = []
                prev = None
                for si in range(start, end):
                    v = samples[si]
                    px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                    if is_analog:
                        py = y0 + ch_h - (float(v) / 4095.0) * ch_h
                    else:
                        py = y0 + (0 if v else ch_h)
                    if prev is not None and v != prev:
                        lpx = self.LABEL_WIDTH + (si - 1 - self.scroll_x) * self.px_scale
                        points.extend([lpx, py, px, py])
                    points.extend([px, py])
                    prev = v
                if points:
                    wf_clr = '#b05a00' if is_analog else '#2a7' if is_filt else '#0066cc'
                    self.create_line(points, fill=wf_clr, width=1.3)
                    if is_analog:
                        self.create_text(w - 4, y0 + 2, text=f"{max(samples[start:end]):04d}",
                                         anchor='ne', font=('Consolas', 7), fill='#b05a00')
                        for v_label, frac in [('3.3V', 1.0), ('1.65V', 0.5), ('0V', 0.0)]:
                            vy = y0 + ch_h - frac * ch_h
                            self.create_text(1, vy, text=v_label, anchor='w',
                                             font=('Consolas', 6), fill='#b05a00')

            self.create_line(0, y0 + ch_h + self.CH_GAP / 2,
                           w, y0 + ch_h + self.CH_GAP / 2,
                           fill='#ddd', dash=(1, 2))

        tot_h = self.total_height()
        measurements = []
        for m, marker in [(self.marker1, 1), (self.marker2, 2)]:
            if m is None:
                continue
            px = self.LABEL_WIDTH + (m - self.scroll_x) * self.px_scale
            self.create_line(px, ruler_h, px, tot_h,
                           fill='red', dash=(4, 2))
            self.create_text(px + 4, ruler_h + 4, text=f"M{marker}",
                            anchor='w', fill='red', font=('Consolas', 8))
            time_ns = m * 1e9 / self.samplerate
            measurements.append((marker, m, time_ns))

        if len(measurements) == 2:
            m1_idx, m1_samp, m1_time = measurements[0]
            m2_idx, m2_samp, m2_time = measurements[1]
            if None in (m1_samp, m2_samp, m1_time, m2_time):
                return
            dt_ns = abs(m2_time - m1_time)
            dsamp = abs(m2_samp - m1_samp)
            freq = 1e9 / dt_ns if dt_ns > 0 else 0
            msr_y = tot_h - self.DECODE_H
            txt = f"Δt = {dt_ns/1000:.1f} µs  ({dsamp} samples)  f = {freq/1000:.1f} kHz"
            self.create_text(self.LABEL_WIDTH + 4, msr_y, text=txt, anchor='w',
                           fill='#c00', font=('Consolas', 9))

    def get_decode_y(self):
        return self.total_height() - self.DECODE_H
