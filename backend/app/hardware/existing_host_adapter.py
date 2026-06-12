"""Adapter wrapping the existing, known-working OLSDeviceSPI host driver.

IMPORTANT: this adapter mirrors the exact call sequence of the proven tkinter
GUI capture path (host/app/OLS_Console.py::_capture). It does not "fix" the
driver's quirks:

  * Digital wire format is 32-bit words with the 16-bit payload in the low
    half (stride 4). Requesting N samples from OLSDeviceSPI.capture() yields
    N/2 effective samples after stride-4 parsing — same as the GUI. We
    therefore request 2x the wanted sample count, matching observed-good
    behaviour rather than re-deriving the divider/count maths.
  * Mixed (digital+analog) capture multiplies the rate/sample count by the
    frame word count exactly as the GUI does, then de-interleaves.

Raw hardware access requires the FTDI D2XX driver (ftd2xx). Import failures
are reported as 'device unavailable' rather than crashing the server.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

import numpy as np

from ..capture.session import CaptureSettings, DeviceMetadata
from ..capture.sample_format import adc_to_volts
from .base import CaptureResult, HardwareDevice, HardwareError, ProgressCb
from .device_models import (DebugInfo, DeviceCapabilities, GeneratorConfig,
                            GeneratorStatus, TriggerCapability)
from .protocol import import_host_driver


def hardware_available() -> bool:
    try:
        ols_spi_device, _ = import_host_driver()
        return bool(ols_spi_device.find_spi_device())
    except Exception:
        return False


class ExistingHostAdapter(HardwareDevice):
    """HardwareDevice implementation backed by host/driver/ols_spi_device.py."""

    def __init__(self) -> None:
        self._dev = None
        self._meta: Optional[DeviceMetadata] = None
        self._gen_cfg: Optional[GeneratorConfig] = None
        self._last_command = ""
        self._last_error = ""
        self._command_log: List[dict] = []
        self._timings: Dict[str, float] = {}
        self._lock = threading.RLock()

    # ── lifecycle ────────────────────────────────────────────────────

    def connect(self) -> DeviceMetadata:
        with self._lock:
            ols_spi_device, _ = import_host_driver()
            try:
                t0 = time.time()
                self._dev = ols_spi_device.OLSDeviceSPI()
                self._dev.open()
                self._timings["open_s"] = time.time() - t0
                self._log("open")
            except Exception as e:
                self._dev = None
                self._last_error = str(e)
                raise HardwareError(f"Failed to open FTDI SPI device: {e}") from e
            meta_raw = b""
            try:
                meta_raw = self._dev.get_metadata()
            except Exception:
                pass
            self._meta = DeviceMetadata(
                driver="ols_spi", device_name="MAX1000 OLS Logic Analyzer",
                connection="FTDI FT2232H MPSSE SPI (Channel B)",
                port="ftdi://channel-b",
                firmware_version=meta_raw.hex() if meta_raw else "unknown",
                protocol_version=str(meta_raw[0]) if meta_raw else "unknown",
                sys_clk_hz=float(self._dev.sys_clk),
                sample_clk_hz=float(self._dev.sample_clk),
                mock=False,
            )
            return self._meta

    def disconnect(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:
                    pass
                self._dev = None
            self._log("close")

    def is_connected(self) -> bool:
        return self._dev is not None

    def get_metadata(self) -> DeviceMetadata:
        if self._meta is None:
            raise HardwareError("Device not connected")
        return self._meta

    def get_capabilities(self) -> DeviceCapabilities:
        sample_clk = float(self._dev.sample_clk) if self._dev else 200e6
        trig = [
            ("none", "hardware", ""),
            ("rising", "hardware", "REG_TRIGGER_MASK edge trigger, any channel set"),
            ("falling", "hardware", "REG_TRIGGER_MASK edge trigger, any channel set"),
            ("uart_byte", "hardware", "Protocol trigger (byte match at baud)"),
            ("any_edge", "post_capture", "Software search after capture"),
            ("high", "post_capture", ""), ("low", "post_capture", ""),
            ("pattern", "post_capture", ""), ("bus_value", "post_capture", ""),
            ("pulse_wider", "post_capture", ""), ("pulse_narrower", "post_capture", ""),
            ("timeout", "post_capture", ""), ("sequence", "post_capture", ""),
            ("i2c_address", "post_capture", ""), ("i2c_nack", "post_capture", ""),
            ("spi_byte", "post_capture", ""), ("glitch", "post_capture", ""),
            ("decoder_error", "post_capture", ""),
        ]
        return DeviceCapabilities(
            digital_channels=16, analog_channels=8,
            max_sample_rate=sample_clk / 2, min_sample_rate=6.0,
            max_samples=1_000_000, bram_samples=1024,
            sample_clk_hz=sample_clk,
            supports_pre_trigger=True, supports_rolling=True,
            supports_continuous=True, supports_analog=True,
            analog_rate_note="MAX10 ADC updates all 8 channels at ~101 kHz; "
                             "digital continues at full rate",
            generator_protocols=["uart", "i2c", "pwm"],
            triggers=[TriggerCapability(type=t, execution=e, description=d)
                      for t, e, d in trig],
            notes=["Schmitt input filter and debug CH0 PWM available via driver"],
        )

    # ── capture (mirrors OLS_Console._capture exactly) ───────────────

    def capture(self, settings: CaptureSettings,
                progress: Optional[ProgressCb] = None,
                stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            dev = self._dev
            rate = float(settings.sample_rate)
            nsamp = int(settings.num_samples)
            trigger = self._build_trigger(settings)
            warnings: List[str] = []

            dev.reset()
            # Auto fast mode: BRAM for small single captures (as the GUI does)
            fast = settings.mode == "single" and nsamp <= 512
            dev.fast_mode_enabled = fast

            def cb(partial, got, total):
                if progress:
                    progress(int(got), int(total), "capturing")

            t0 = time.time()
            self._log(f"capture rate={rate:.0f} nsamp={nsamp} trigger={trigger}")
            try:
                if settings.analog_enabled:
                    # Mixed 16-digital + 8-ADC mode — same maths as the GUI.
                    from driver.ols_spi_device import (MODE_MIXED,
                                                       analog_frame_stride,
                                                       decode_analog_frames,
                                                       wire_to_payload)
                    stride = analog_frame_stride(MODE_MIXED)      # 14
                    words_per_frame = stride // 2                 # 7
                    dev.set_analog_config(MODE_MIXED)
                    sdram_words = nsamp * words_per_frame
                    wire = dev.capture(
                        rate_hz=rate * words_per_frame,
                        nsamples=sdram_words * 2,
                        timeout=max(3, sdram_words // 10000 + 2),
                        trigger=trigger, stop_evt=stop_evt, progress_cb=cb)
                    payload = wire_to_payload(wire)[: nsamp * stride]
                    frames = decode_analog_frames(payload, MODE_MIXED)
                    digital = np.array([fr["digital"] for fr in frames],
                                       dtype=np.uint16)
                    analog = {}
                    if frames:
                        adc = np.array([fr["adc"] for fr in frames],
                                       dtype=np.uint16)
                        for ch in range(adc.shape[1]):
                            analog[f"a{ch}"] = adc_to_volts(adc[:, ch])
                else:
                    dev.set_analog_config(0)
                    pre = settings.trigger.pre_trigger_samples
                    data = dev.capture(
                        rate_hz=rate, nsamples=nsamp,
                        timeout=max(3, nsamp // 10000 + 2),
                        trigger=trigger, stop_evt=stop_evt,
                        progress_cb=cb, pre_trigger=pre)
                    if not data:
                        raise HardwareError(
                            "Capture returned 0 bytes — FPGA not responding")
                    # GUI-equivalent stride-4 parse: 32-bit words, low 16 = payload
                    n4 = len(data) - (len(data) % 4)
                    words = np.frombuffer(data[:n4], dtype="<u4")
                    digital = (words & 0xFFFF).astype(np.uint16)
                    analog = {}
                    if len(digital) < nsamp:
                        warnings.append(
                            f"Device returned {len(digital)} effective samples "
                            f"for {nsamp} requested (existing host wire format)")
            except HardwareError:
                raise
            except Exception as e:
                self._last_error = str(e)
                raise HardwareError(f"Capture failed: {e}") from e
            self._timings["last_capture_s"] = time.time() - t0

            trigger_sample = None
            if trigger is not None and settings.trigger.pre_trigger_samples:
                trigger_sample = min(settings.trigger.pre_trigger_samples,
                                     len(digital))
            return CaptureResult(
                sample_rate=rate, digital=digital, analog=analog,
                trigger_sample=trigger_sample,
                divider=max(0, round(dev.sample_clk / rate) - 1),
                warnings=warnings)

    def _build_trigger(self, settings: CaptureSettings):
        trig = settings.trigger
        if trig.type in ("rising", "falling") and trig.channels:
            mode_bits = (1 if trig.type == "rising" else 2) << 30
            ch_mask = 0
            for c in trig.channels:
                ch_mask |= 1 << c
            return mode_bits | ch_mask
        if trig.type == "uart_byte" and trig.value is not None:
            # Configured via trigger_decode just before arm
            dev = self._dev
            ch = trig.channels[0] if trig.channels else 0
            dev.trigger_decode(match_byte=trig.value & 0xFF, channel=ch,
                               baud=trig.baud or 115200, enable=True)
            return None
        return None

    # ── generator ────────────────────────────────────────────────────

    def generator_status(self) -> GeneratorStatus:
        busy = False
        if self._dev is not None:
            try:
                st = self._dev.pkt.get_status()
                busy = bool(st.get("gen_busy", False))
            except Exception:
                pass
        return GeneratorStatus(busy=busy, running=busy,
                               protocol=self._gen_cfg.protocol if self._gen_cfg else None,
                               supported=True,
                               detail="UART/I2C generator + debug CH0 PWM (FPGA)")

    def generator_configure(self, cfg: GeneratorConfig) -> None:
        if cfg.protocol not in ("uart", "i2c", "pwm"):
            raise HardwareError(
                f"Generator protocol '{cfg.protocol}' is not supported by the "
                "current FPGA firmware (supported: uart, i2c, pwm)")
        self._gen_cfg = cfg

    def generator_start(self) -> None:
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            cfg = self._gen_cfg
            if cfg is None:
                raise HardwareError("Generator not configured")
            data = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"
            self._log(f"gen_start {cfg.protocol}")
            if cfg.protocol == "uart":
                self._dev.send_uart(data, baud=cfg.baud, tx_pin=cfg.tx_pin)
            elif cfg.protocol == "i2c":
                self._dev.i2c_read_setup(cfg.i2c_address, cfg.i2c_register,
                                         read_len=cfg.i2c_read_len,
                                         speed=cfg.baud, tx_pin=cfg.tx_pin,
                                         scl_pin=cfg.scl_pin)
                self._dev.start_gen()
            elif cfg.protocol == "pwm":
                self._dev.set_debug_ch0(True, freq_hz=cfg.freq_hz,
                                        duty_pct=cfg.duty_pct)

    def generator_stop(self) -> None:
        with self._lock:
            if self._dev is None:
                return
            if self._gen_cfg and self._gen_cfg.protocol == "pwm":
                self._dev.set_debug_ch0(False)
            self._log("gen_stop")

    def capture_with_generator(self, settings: CaptureSettings, cfg: GeneratorConfig,
                               progress: Optional[ProgressCb] = None,
                               stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        """Atomic generator+capture via the proven CMD_GEN_CAPTURE path."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            dev = self._dev
            data = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"
            rate = float(settings.sample_rate)
            nsamp = int(settings.num_samples)
            self._log(f"gen_capture {cfg.protocol}")

            def cb(partial, got, total):
                if progress:
                    progress(int(got), int(total), "capturing")

            if cfg.protocol == "i2c":
                raw = dev.i2c_capture_with_gen(
                    rate_hz=rate, nsamples=nsamp, i2c_speed=cfg.baud,
                    dev_addr=cfg.i2c_address, reg_addr=cfg.i2c_register,
                    read_len=cfg.i2c_read_len, tx_pin=cfg.tx_pin,
                    scl_pin=cfg.scl_pin)
            elif cfg.protocol == "uart":
                dev._gen_data = data
                dev._gen_baud = cfg.baud
                dev._gen_tx_pin = cfg.tx_pin
                raw = dev.capture_with_gen(rate_hz=rate, nsamples=nsamp,
                                           stop_evt=stop_evt, progress_cb=cb)
            else:
                raise HardwareError(
                    f"Loopback capture not supported for '{cfg.protocol}' on hardware")
            if not raw:
                raise HardwareError("Generator capture returned no data")
            n4 = len(raw) - (len(raw) % 4)
            words = np.frombuffer(raw[:n4], dtype="<u4")
            digital = (words & 0xFFFF).astype(np.uint16)
            return CaptureResult(sample_rate=rate, digital=digital)

    # ── diagnostics ──────────────────────────────────────────────────

    def get_debug_info(self) -> DebugInfo:
        raw_meta = ""
        raw_status: Dict = {}
        if self._dev is not None:
            try:
                raw_meta = self._dev.get_metadata().hex()
                raw_status = self._dev.pkt.get_status()
            except Exception as e:
                self._last_error = str(e)
        return DebugInfo(raw_metadata=raw_meta, raw_status=raw_status,
                         last_command=self._last_command,
                         last_error=self._last_error,
                         command_log=self._command_log[-50:],
                         timings=self._timings)

    def self_test(self) -> dict:
        checks = []
        if self._dev is None:
            return {"passed": False, "checks": [],
                    "message": "Connect the device first"}
        with self._lock:
            try:
                meta = self._dev.get_metadata()
                checks.append({"name": "metadata", "passed": len(meta) >= 2,
                               "detail": meta.hex() or "empty"})
            except Exception as e:
                checks.append({"name": "metadata", "passed": False, "detail": str(e)})
            try:
                st = self._dev.pkt.get_status()
                checks.append({"name": "status", "passed": bool(st),
                               "detail": str(st)})
            except Exception as e:
                checks.append({"name": "status", "passed": False, "detail": str(e)})
            try:
                # Debug CH0 loopback: enable PWM, tiny capture, expect edges on CH0
                self._dev.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
                raw = self._dev.capture(rate_hz=1_000_000, nsamples=1024, timeout=4)
                self._dev.set_debug_ch0(False)
                n4 = len(raw) - (len(raw) % 4)
                words = np.frombuffer(raw[:n4], dtype="<u4") & 1
                edges = int(np.count_nonzero(np.diff(words)))
                checks.append({"name": "ch0_loopback", "passed": edges > 2,
                               "detail": f"{edges} CH0 edges with debug PWM on"})
            except Exception as e:
                checks.append({"name": "ch0_loopback", "passed": False,
                               "detail": str(e)})
        return {"passed": all(c["passed"] for c in checks), "checks": checks,
                "message": "Hardware self-test complete "
                           "(full suite: python -m app.hw_validation)"}

    def _log(self, cmd: str) -> None:
        self._last_command = cmd
        self._command_log.append({"t": time.time(), "cmd": cmd})
        if len(self._command_log) > 500:
            self._command_log = self._command_log[-250:]
