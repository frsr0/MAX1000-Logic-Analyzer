"""Fully functional mock device — exercises the entire app without hardware.

Scenario waveforms are deterministic so decoder/measurement tests can assert
exact results. Mock analog channels exist ONLY here; the real-hardware adapter
never fabricates analog data.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

import numpy as np

from ..capture.session import CaptureSettings, DeviceMetadata
from .base import CaptureResult, HardwareDevice, HardwareError, ProgressCb
from .device_models import (DebugInfo, DeviceCapabilities, GeneratorConfig,
                            GeneratorStatus, TriggerCapability)
from . import mock_signals as ms

SCENARIOS = [
    {"id": "demo_mixed", "name": "Demo: counters + UART + I2C + SPI + PWM"},
    {"id": "square_waves", "name": "Square waves (per-channel frequencies)"},
    {"id": "uart", "name": "UART frames on CH0 ('Hello MAX1000!')"},
    {"id": "i2c", "name": "I2C transaction (SCL=CH1, SDA=CH2)"},
    {"id": "spi", "name": "SPI transaction (SCLK/MOSI/MISO/CS = CH4-7)"},
    {"id": "pwm", "name": "PWM sweep on CH3"},
    {"id": "glitchy", "name": "Noisy/glitchy square on CH0"},
    {"id": "edge_cases", "name": "All-zero CH14, all-one CH15, slow CH0"},
    {"id": "analog_demo", "name": "Analog: sine/square/ramp/noise (mixed mode)"},
    {"id": "long_stress", "name": "Long capture stress test"},
]


class MockDevice(HardwareDevice):
    SAMPLE_CLK = 200e6

    def __init__(self) -> None:
        self._connected = False
        self._gen_cfg: Optional[GeneratorConfig] = None
        self._gen_running = False
        self._gen_error: Optional[str] = None
        self._last_command = ""
        self._command_log: List[dict] = []
        self._capture_count = 0

    # ── lifecycle ────────────────────────────────────────────────────

    def connect(self) -> DeviceMetadata:
        self._connected = True
        self._log("connect")
        return self.get_metadata()

    def disconnect(self) -> None:
        self._connected = False
        self._log("disconnect")

    def is_connected(self) -> bool:
        return self._connected

    def get_metadata(self) -> DeviceMetadata:
        return DeviceMetadata(
            driver="mock", device_name="Mock MAX1000 Analyser",
            connection="mock", port="mock://0",
            firmware_version="mock-2.0", protocol_version="2",
            sys_clk_hz=100e6, sample_clk_hz=self.SAMPLE_CLK, mock=True,
            extra={"scenarios": SCENARIOS},
        )

    def get_capabilities(self) -> DeviceCapabilities:
        hw = [("rising", "hardware"), ("falling", "hardware"),
              ("any_edge", "hardware"), ("none", "hardware"),
              ("high", "post_capture"), ("low", "post_capture"),
              ("pattern", "post_capture"), ("bus_value", "post_capture"),
              ("pulse_wider", "post_capture"), ("pulse_narrower", "post_capture"),
              ("timeout", "post_capture"), ("sequence", "post_capture"),
              ("uart_byte", "hardware"), ("i2c_address", "post_capture"),
              ("i2c_nack", "post_capture"), ("spi_byte", "post_capture"),
              ("glitch", "post_capture"), ("decoder_error", "post_capture")]
        return DeviceCapabilities(
            digital_channels=16, analog_channels=8,
            max_sample_rate=100e6, min_sample_rate=10.0,
            max_samples=2_000_000, bram_samples=1024,
            sample_clk_hz=self.SAMPLE_CLK,
            supports_pre_trigger=True, supports_rolling=True,
            supports_continuous=True, supports_analog=True,
            analog_rate_note="Mock analog channels (real hardware: ~101 kHz ADC update)",
            generator_protocols=["uart", "i2c", "spi", "pwm", "square",
                                 "pattern", "counter", "prbs"],
            triggers=[TriggerCapability(type=t, execution=e) for t, e in hw],
            notes=["Mock device — all data is synthetic"],
        )

    # ── capture ──────────────────────────────────────────────────────

    def capture(self, settings: CaptureSettings,
                progress: Optional[ProgressCb] = None,
                stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        if not self._connected:
            raise HardwareError("Mock device not connected")
        self._log(f"capture {settings.num_samples}@{settings.sample_rate:.0f}Hz "
                  f"scenario={settings.mock_scenario}")
        n = int(settings.num_samples)
        rate = float(settings.sample_rate)
        scenario = settings.mock_scenario or "demo_mixed"

        digital, analog = self._build_scenario(scenario, n, rate,
                                               settings.analog_enabled)
        # simulate capture time (bounded so tests stay fast)
        total_time = min(n / rate, 1.5)
        steps = 10
        for i in range(steps):
            if stop_evt is not None and stop_evt.is_set():
                raise HardwareError("Capture cancelled")
            time.sleep(total_time / steps)
            if progress:
                phase = "capturing" if i < steps - 2 else "reading"
                progress(int(n * (i + 1) / steps), n, phase)

        trigger_sample = None
        trig = settings.trigger
        if trig.type in ("rising", "falling", "any_edge") and trig.channels:
            ch = trig.channels[0]
            bits = ((digital >> ch) & 1).astype(np.int8)
            d = np.diff(bits)
            want = d > 0 if trig.type == "rising" else (
                d < 0 if trig.type == "falling" else d != 0)
            hits = np.nonzero(want)[0]
            if len(hits):
                trigger_sample = int(hits[0] + 1)

        self._capture_count += 1
        return CaptureResult(sample_rate=rate, digital=digital, analog=analog,
                             trigger_sample=trigger_sample,
                             divider=max(0, int(self.SAMPLE_CLK / rate / 2) - 1))

    def _build_scenario(self, scenario: str, n: int, rate: float,
                        analog_enabled: bool):
        digital = np.zeros(n, dtype=np.uint16)
        analog: Dict[str, np.ndarray] = {}

        def put(ch: int, bits: np.ndarray) -> None:
            digital_ref = bits.astype(np.uint16) << ch
            np.bitwise_or(digital, digital_ref, out=digital)

        if scenario in ("demo_mixed", "long_stress"):
            put(0, ms.uart_signal(n, rate, max(9600, int(rate / 87)),
                                  b"Hello MAX1000!", start_sample=n // 20))
            scl, sda = ms.i2c_signal(n, rate, max(1000.0, rate / 220), 0x3C, True,
                                     b"\x10\xA5", start_sample=n // 3)
            put(1, scl)
            put(2, sda)
            put(3, ms.square(n, rate, rate / 160, duty=0.30))
            sclk, mosi, miso, cs = ms.spi_signal(
                n, rate, max(1000.0, rate / 64), b"\xDE\xAD\xBE\xEF",
                b"\x12\x34\x56\x78", start_sample=int(n * 0.62))
            put(4, sclk); put(5, mosi); put(6, miso); put(7, cs)
            for i in range(8, 14):
                put(i, ms.square(n, rate, rate / (2 ** (i - 2))))
            put(15, np.ones(n, dtype=np.uint8))
        elif scenario == "square_waves":
            for i in range(16):
                put(i, ms.square(n, rate, rate / (8 * (i + 1))))
        elif scenario == "uart":
            put(0, ms.uart_signal(n, rate, max(300, int(rate / 100)),
                                  b"Hello MAX1000!", start_sample=n // 10))
            put(1, ms.square(n, rate, rate / 100))
        elif scenario == "i2c":
            scl, sda = ms.i2c_signal(n, rate, max(100.0, rate / 200), 0x3C, True,
                                     b"\x10\xA5\x42", start_sample=n // 10,
                                     ack_per_byte=[True, True, True, False])
            put(1, scl)
            put(2, sda)
        elif scenario == "spi":
            sclk, mosi, miso, cs = ms.spi_signal(
                n, rate, max(100.0, rate / 40), b"\xDE\xAD\xBE\xEF",
                b"\xCA\xFE\xBA\xBE", start_sample=n // 10)
            put(4, sclk); put(5, mosi); put(6, miso); put(7, cs)
        elif scenario == "pwm":
            third = n // 3
            duties = [0.2, 0.5, 0.8]
            sig = np.concatenate([
                ms.square(third if k < 2 else n - 2 * third, rate, rate / 120,
                          duty=duties[k]) for k in range(3)])
            put(3, sig[:n])
        elif scenario == "glitchy":
            put(0, ms.glitchy_signal(n, rate, rate / 130))
            put(1, ms.square(n, rate, rate / 130))
        elif scenario == "edge_cases":
            put(0, ms.square(n, rate, max(2.0, rate / max(4, n))))
            put(15, np.ones(n, dtype=np.uint8))
            # CH14 stays all-zero
        elif scenario == "analog_demo":
            analog_enabled = True
        else:
            put(0, ms.square(n, rate, rate / 100))

        if analog_enabled or scenario == "analog_demo":
            analog["a0"] = ms.sine_wave(n, rate, rate / 240, noise=0.01)
            analog["a1"] = ms.analog_square(n, rate, rate / 300)
            analog["a2"] = ms.ramp_wave(n, rate, rate / 500)
            analog["a3"] = ms.sine_wave(n, rate, rate / 180, amplitude=0.4,
                                        offset=1.0, noise=0.15, seed=7)
            if scenario == "analog_demo":
                put(0, ms.square(n, rate, rate / 240))     # aligned with a0
                put(1, (analog["a0"] > 1.65).astype(np.uint8))
        return digital, analog

    # ── generator (loopback simulation) ──────────────────────────────

    def generator_status(self) -> GeneratorStatus:
        return GeneratorStatus(busy=self._gen_running, running=self._gen_running,
                               protocol=self._gen_cfg.protocol if self._gen_cfg else None,
                               last_error=self._gen_error, supported=True,
                               detail="Mock generator — loopback into capture")

    def generator_configure(self, cfg: GeneratorConfig) -> None:
        self._gen_cfg = cfg
        self._gen_error = None
        self._log(f"gen_configure {cfg.protocol}")

    def generator_start(self) -> None:
        if self._gen_cfg is None:
            raise HardwareError("Generator not configured")
        self._gen_running = True
        self._log("gen_start")
        if not self._gen_cfg.continuous:
            def stop_later():
                time.sleep(0.3)
                self._gen_running = False
            threading.Thread(target=stop_later, daemon=True).start()

    def generator_stop(self) -> None:
        self._gen_running = False
        self._log("gen_stop")

    def capture_with_generator(self, settings: CaptureSettings, cfg: GeneratorConfig,
                               progress: Optional[ProgressCb] = None,
                               stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        """Loopback: render the configured generator output into the capture."""
        if not self._connected:
            raise HardwareError("Mock device not connected")
        self._log(f"gen_capture {cfg.protocol}")
        n = int(settings.num_samples)
        rate = float(settings.sample_rate)
        digital = np.zeros(n, dtype=np.uint16)
        data = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"

        def put(ch, bits):
            np.bitwise_or(digital, bits.astype(np.uint16) << ch, out=digital)

        start = n // 10
        if cfg.protocol == "uart":
            put(cfg.tx_pin, ms.uart_signal(n, rate, cfg.baud, data, start))
        elif cfg.protocol == "i2c":
            scl, sda = ms.i2c_signal(n, rate, cfg.baud, cfg.i2c_address, True,
                                     bytes([cfg.i2c_register]) + data, start)
            put(cfg.scl_pin, scl)
            put(cfg.tx_pin, sda)
        elif cfg.protocol == "spi":
            sclk, mosi, miso, cs = ms.spi_signal(n, rate, cfg.baud, data,
                                                 start_sample=start)
            put(4, sclk); put(5, mosi); put(6, miso); put(7, cs)
        elif cfg.protocol in ("pwm", "square"):
            put(cfg.tx_pin, ms.square(n, rate, cfg.freq_hz, cfg.duty_pct / 100))
        elif cfg.protocol == "counter":
            counter = (np.arange(n, dtype=np.uint32) // 16) & 0xFFFF
            digital = counter.astype(np.uint16)
        elif cfg.protocol == "prbs":
            rng = np.random.default_rng(12345)
            put(cfg.tx_pin, rng.integers(0, 2, n).astype(np.uint8))
        elif cfg.protocol == "pattern":
            bits = np.zeros(n, dtype=np.uint8)
            spb = max(1, int(rate / max(1, cfg.baud)))
            for i, byte in enumerate(data):
                for b in range(8):
                    a = start + (i * 8 + b) * spb
                    if a >= n:
                        break
                    bits[a:a + spb] = (byte >> (7 - b)) & 1
            put(cfg.tx_pin, bits)
        else:
            raise HardwareError(f"Unknown generator protocol: {cfg.protocol}")

        time.sleep(min(0.3, n / rate))
        if progress:
            progress(n, n, "reading")
        return CaptureResult(sample_rate=rate, digital=digital,
                             trigger_sample=None)

    # ── diagnostics ──────────────────────────────────────────────────

    def get_debug_info(self) -> DebugInfo:
        return DebugInfo(
            raw_metadata="mock device, protocol v2, sample_clk=200 MHz",
            raw_status={"connected": self._connected,
                        "captures_run": self._capture_count,
                        "gen_running": self._gen_running},
            last_command=self._last_command,
            command_log=self._command_log[-50:],
        )

    def self_test(self) -> dict:
        checks = [
            {"name": "connectivity", "passed": self._connected,
             "detail": "mock link ok" if self._connected else "not connected"},
            {"name": "memory", "passed": True, "detail": "synthetic buffers ok"},
            {"name": "generator", "passed": True, "detail": "loopback path ok"},
        ]
        return {"passed": all(c["passed"] for c in checks), "checks": checks,
                "message": "Mock self-test complete"}

    def _log(self, cmd: str) -> None:
        self._last_command = cmd
        self._command_log.append({"t": time.time(), "cmd": cmd})
        if len(self._command_log) > 500:
            self._command_log = self._command_log[-250:]
