"""Adapter wrapping the existing, known-working OLSDeviceSPI host driver,
delegating capture modes to mode-specific CaptureStrategy classes.

The strategies live in hardware/strategies/ and implement a common interface:
each receives a CaptureDevice protocol and CaptureSettings, performs one
capture attempt, and the base class handles retry/recovery.

Raw hardware access requires the FTDI D2XX driver (ftd2xx). Import failures
are reported as 'device unavailable' rather than crashing the server.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from ..capture.session import CaptureSettings, DeviceMetadata
from ..capture.sample_format import adc_to_volts
from .base import CaptureResult, HardwareDevice, HardwareError, ProgressCb
from .device_models import (DebugInfo, DeviceCapabilities, GeneratorConfig,
                            GeneratorRouteCapability, GeneratorStatus,
                            TriggerCapability)
from .max1000_board import (
    BOARD_ANALOG_INPUTS,
    DIGITAL_PIN_MAP,
    exposed_analog_count_for_current_rtl,
)
from .protocol import import_host_driver
from .strategies.base import CaptureDevice, CaptureStrategy
from .strategies import (
    digital as _digital_strategy,
    mixed as _mixed_strategy,
    analog as _analog_strategy,
    analog_all as _analog_all_strategy,
    narrow_digital as _narrow_digital_strategy,
)
from .packed_decoder import decode as packed_decode
from ..triggers.hardware_support import to_register_config
from ..triggers.software_trigger import project_generic_pattern_for_hardware

ADC_SCAN_FRAME_RATE_HZ = 125_000.0
ADC_FAST_FRAME_RATE_HZ = 1_000_000.0
# The live rolling UI is built on repeated finite captures, not the old
# retention-ring path. 50 MHz is the tested ceiling for that user-facing live
# mode on this board revision.
DIGITAL_LIVE_SAMPLE_RATE_HZ = 50_000_000.0
DIGITAL_FAST_BRAM_SAMPLES = 1024
# Full 64 Mbit x16 SDRAM = 4,194,304 words. Raised from 1M after the deep-capture
# write-path fix: hardware-validated end-to-end (1M/2M/4M captures all return 100%
# of samples with 0 drops). 1M was the conservative pre-fix ceiling.
DIGITAL_SDRAM_WORDS = 4_194_304
DIGITAL_NARROW_LOGICAL_SAMPLES = DIGITAL_SDRAM_WORDS * 16


def _adapt_driver_progress(progress: Optional[ProgressCb]) -> Optional[Callable[..., None]]:
    """Translate the host driver's raw-buffer callback to the backend contract.

    The host driver reports ``(partial_bytes, samples_read, buffer_samples)``.
    Backend devices report ``(samples_read, samples_total, phase)``.  Keeping
    this conversion at the hardware boundary prevents raw sample buffers from
    leaking into API status and WebSocket progress messages.  The host driver's
    callback contract is explicit here; do not infer a contract from payload
    types because a byte count can be numeric too.
    """
    if progress is None:
        return None

    def report(_partial: Any, total: Any, buffer_samples: Any) -> None:
        progress(int(total), int(buffer_samples), "capturing")

    return report


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
                t1 = time.time()
                self._reset_after_connect()
                self._timings["connect_reset_s"] = time.time() - t1
            except Exception as e:
                if self._dev is not None:
                    try:
                        self._dev.close()
                    except Exception:
                        pass
                self._dev = None
                self._last_error = str(e)
                raise HardwareError(
                    f"Failed to open/reset FTDI SPI device: {e}") from e
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

    def _reset_after_connect(self) -> None:
        """Leave a newly opened FPGA connection in a known idle state."""
        assert self._dev is not None
        self._dev.reset()
        self._dev.set_analog_config(0)
        self._dev.set_schmitt(False)
        self._dev.spi.flush()
        self._log("connect_reset")

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

    # ── accelerometer bus seam ─────────────────────────────────────

    def accelerometer_probe(self) -> Optional[int]:
        """Find the LIS3DH address while owning the shared SPI link."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            for address in (0x19, 0x18):
                try:
                    if self._dev.accel_read_i2c(0x0F, dev_addr=address) == 0x33:
                        return address
                except Exception:
                    continue
            return None

    def accelerometer_configure(self, address: int, config=None) -> None:
        """Configure the LIS3DH atomically with respect to captures."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            writer = getattr(self._dev, "accel_write_i2c", None)
            if not callable(writer):
                raise HardwareError("Connected hardware cannot configure LIS3DH")
            if config is None:
                ctrl1, ctrl4 = 0x27, 0x88
            else:
                # Keep the adapter's hardware write path identical to the
                # service's readback decoder. In particular, CTRL_REG1 code 9
                # is 1.344 kHz in normal/HR mode but 5.376 kHz in low power.
                # Import lazily to avoid coupling host adapter construction to
                # the MIL service module's process-global instance.
                from ..mil.accelerometer import _config_registers
                ctrl1, ctrl4 = _config_registers(config)
            for register, value in ((0x20, ctrl1), (0x23, ctrl4)):
                if writer(register, value, dev_addr=address) is False:
                    raise HardwareError(
                        f"Failed to configure LIS3DH register 0x{register:02x}")

    def accelerometer_read_xyz(self, address: int) -> bytes:
        """Read one coherent six-byte XYZ burst while holding the link lock."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            try:
                payload = self._dev.accel_read_i2c(
                    0x28, dev_addr=address, read_len=6)
            except TypeError:
                payload = bytes(
                    self._dev.accel_read_i2c(0x28 + offset, dev_addr=address)
                    for offset in range(6))
            if not isinstance(payload, (bytes, bytearray)) or len(payload) != 6:
                raise HardwareError("LIS3DH returned an invalid XYZ sample")
            return bytes(payload)

    def accelerometer_fifo_configure(self, address: int, enabled: bool = True,
                                     mode: str = "stream",
                                     threshold: int = 31,
                                     speed: int = 400_000):
        """Configure the optional LIS3DH FIFO through the host driver.

        Older host driver images do not expose this seam.  Returning ``None``
        in that case lets the MIL service retain its atomic six-byte fallback
        instead of treating an optional acceleration feature as a hard device
        failure.
        """
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            configure = getattr(self._dev, "accel_fifo_configure", None)
            if not callable(configure):
                return None
            return configure(
                enabled=bool(enabled), dev_addr=int(address), mode=mode,
                threshold=int(threshold), speed=int(speed))

    def accelerometer_capture_cleanup(self) -> None:
        """Clear the persistent live-capture attach route after teardown."""
        with self._lock:
            if self._dev is None:
                return
            cleanup = getattr(self._dev, "accel_capture_cleanup", None)
            if callable(cleanup):
                cleanup()

    def accelerometer_read_fifo(self, address: int, max_samples: int = 4,
                                speed: int = 400_000):
        """Drain a bounded batch of FIFO XYZ frames, if supported."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            reader = getattr(self._dev, "accel_read_fifo", None)
            if not callable(reader):
                return None
            try:
                payload = reader(
                    dev_addr=int(address), max_samples=max(1, int(max_samples)),
                    speed=int(speed))
            except TypeError:
                # Older out-of-tree host drivers may not accept the optional
                # speed keyword; preserve their original FIFO seam.
                payload = reader(
                    dev_addr=int(address), max_samples=max(1, int(max_samples)))
            if payload is None:
                return None
            if not isinstance(payload, (bytes, bytearray)) \
                    or len(payload) % 6:
                raise HardwareError("LIS3DH returned an invalid FIFO payload")
            return bytes(payload)

    def accelerometer_fifo_status(self, address: int):
        """Return the last/read LIS3DH FIFO status when the seam exists."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            reader = getattr(self._dev, "accel_fifo_status", None)
            if not callable(reader):
                return None
            return reader(dev_addr=int(address))

    def accelerometer_read_config(self, address: int) -> dict[str, int]:
        """Read back LIS3DH control registers after configuration."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            reader = getattr(self._dev, "accel_read_i2c", None)
            if not callable(reader):
                raise HardwareError("Connected hardware cannot read LIS3DH config")
            ctrl1 = reader(0x20, dev_addr=address)
            ctrl4 = reader(0x23, dev_addr=address)
            if not isinstance(ctrl1, int) or not isinstance(ctrl4, int):
                raise HardwareError("LIS3DH returned invalid configuration registers")
            return {"ctrl1": ctrl1 & 0xFF, "ctrl4": ctrl4 & 0xFF}

    def get_capabilities(self) -> DeviceCapabilities:
        sample_clk = float(self._dev.sample_clk) if self._dev else 200e6
        trig = [
            ("none", "hardware", ""),
            ("rising", "hardware", "REG_TRIGGER_MASK edge trigger, any channel set"),
            ("falling", "hardware", "REG_TRIGGER_MASK edge trigger, any channel set"),
            ("uart_byte", "post_capture", "UART byte match, decoded after capture (no FPGA protocol-trigger block)"),
            ("generic_pattern", "hardware", "Selectable coarse hardware trigger with full-channel software refinement"),
            ("any_edge", "post_capture", "Software search after capture"),
            ("high", "hardware", "REG_TRIGGER_MASK level trigger: all selected channels high"),
            ("low", "hardware", "REG_TRIGGER_MASK level trigger: all selected channels low"),
            ("pattern", "hardware", "REG_TRIGGER_MASK level trigger with masked bits"),
            ("bus_value", "hardware", "REG_TRIGGER_MASK level trigger on selected bus bits"),
            ("pulse_wider", "post_capture", ""), ("pulse_narrower", "post_capture", ""),
            ("timeout", "post_capture", ""), ("sequence", "post_capture", ""),
            ("i2c_address", "post_capture", ""), ("i2c_nack", "post_capture", ""),
            ("spi_byte", "post_capture", ""), ("glitch", "post_capture", ""),
            ("decoder_error", "post_capture", ""),
        ]
        return DeviceCapabilities(
            digital_channels=16,
            analog_channels=exposed_analog_count_for_current_rtl(),
            max_sample_rate=sample_clk, min_sample_rate=6.0,
            max_samples=DIGITAL_SDRAM_WORDS, bram_samples=1024,
            sample_clk_hz=sample_clk,
            supports_pre_trigger=True, supports_rolling=True,
            supports_continuous=True, supports_analog=True,
            analog_rate_note="MAX10 ADC supports 1 MSPS single-channel "
                             "analog (measured ~99.5 kS/s on this image) and "
                             "a packed 4-input physical analog scan at "
                             "~24 kS/s per lane.",
            generator_protocols=["uart", "rs485", "i2c", "spi", "swd", "bitbang"],
            generator_routes=[
                GeneratorRouteCapability(
                    protocol="uart", name="UART TX", physical=True,
                    outputs={"tx": "configurable"},
                    features=["capture_loopback"],
                ),
                GeneratorRouteCapability(
                    protocol="rs485", name="RS-485 A/B", physical=True,
                    outputs={"a": "configurable", "b": "configurable",
                             "de": "configurable_gpio"},
                    features=["capture_loopback", "internal_de_timing", "de_pin"],
                    detail="DE is driven high for the active Bit_Engine burst; omit it when using the on-board transceiver direction path.",
                ),
                GeneratorRouteCapability(
                    protocol="i2c", name="I²C", physical=True,
                    outputs={"sda": "configurable", "scl": "configurable"},
                    features=["external_slave"],
                ),
                GeneratorRouteCapability(
                    protocol="spi", name="SPI MOSI/SCLK/CS/MISO", physical=True,
                    outputs={"mosi": "configurable", "sclk": "configurable",
                             "cs": "configurable_gpio", "miso": "configurable_input"},
                    features=["capture_loopback", "cs", "miso", "cs_pin", "miso_pin", "fixed_sensor_cs_miso"],
                    detail="CS can use a GPIO or the on-board sensor CS; MISO can use a GPIO input or the on-board sensor SDO (pin 23).",
                ),
                GeneratorRouteCapability(
                    protocol="bitbang", name="Two-output Bit Banger", physical=True,
                    outputs={"data": "configurable", "clock": "configurable"},
                    features=["raw_symbols"],
                ),
                GeneratorRouteCapability(
                    protocol="swd", name="SWD SWCLK/SWDIO", physical=True,
                    outputs={"swclk": "configurable", "swdio": "configurable"},
                    features=["transaction_capture"],
                    detail="Uses the existing two-output Bit Banger capture route; target response is observable only when electrically connected.",
                ),
            ],
            triggers=[TriggerCapability(type=t, execution=e, description=d)
                      for t, e, d in trig],
            notes=[
                "Host-side digital glitch filter (a.k.a. Schmitt) is available; Bit Engine PWM provides an internal waveform source for capture self-tests.",
                "The MAX1000 has 64 Mbit SDRAM. This bitstream exposes a "
                f"{DIGITAL_SDRAM_WORDS:,}-word 16-bit SDRAM capture ring "
                f"({DIGITAL_NARROW_LOGICAL_SAMPLES:,} logical samples in "
                "packed one-channel narrow mode).",
                "Maximum analog scans AIN3, AIN1, AIN4, and AIN6 at ~24 kS/s "
                "per lane via the packed MSO path. "
                "Mixed mode streams 16 digital bits plus the reduced two-result "
                "ADC frame (ADC1/AIN3 and ADC2/AIN1) at ~125 kframes/s.",
            ],
            digital_pin_map=DIGITAL_PIN_MAP,
            analog_pin_map=BOARD_ANALOG_INPUTS,
        )

    # ── strategy dispatch ────────────────────────────────────────────

    _STRATEGY_REGISTRY: list[type[CaptureStrategy]] = [
        _digital_strategy.DigitalCaptureStrategy,
        _mixed_strategy.MixedCaptureStrategy,
        _analog_strategy.AnalogCaptureStrategy,
        _analog_all_strategy.AnalogAllCaptureStrategy,
        _narrow_digital_strategy.NarrowDigitalCaptureStrategy,
    ]

    @classmethod
    def _strategy_for(cls, settings: CaptureSettings) -> Optional[CaptureStrategy]:
        """Return the strategy handling *settings.mode*, or None if no match."""
        for stype in cls._STRATEGY_REGISTRY:
            if settings.mode in stype.modes:
                return stype()
        return None

    # ── capture (mirrors OLS_Console._capture exactly) ───────────────

    def validate_settings(self, settings: CaptureSettings) -> list:
        findings = super().validate_settings(settings)
        if settings.mode == "digital_narrow":
            findings = [
                f for f in findings
                if "exceeds capture depth" not in f.get("message", "")
            ]
            if settings.num_samples > DIGITAL_NARROW_LOGICAL_SAMPLES:
                findings.append({
                    "level": "error",
                    "message": f"{settings.num_samples} narrow samples exceeds "
                               f"packed capture depth "
                               f"{DIGITAL_NARROW_LOGICAL_SAMPLES}",
                })
        sample_clk = float(self._dev.sample_clk) if self._dev else 200_000_000.0
        if (settings.trigger.type in {"high", "low", "pattern", "bus_value"}
                and to_register_config(settings.trigger) is None):
            findings.append({
                "level": "error",
                "message": f"Invalid {settings.trigger.type} hardware trigger: "
                           "select channels and provide a valid matching value",
            })
        if settings.mode in ("mixed", "mixed_continuous"):
            findings.append({
                "level": "info",
                "message": "Mixed mode captures 16 digital bits plus the current "
                           "two-result ADC path as a single time-correlated "
                           "packed frame at up to "
                           f"{int(ADC_SCAN_FRAME_RATE_HZ):,} Hz. Digital is "
                           "sampled once per ADC frame; use digital-only mode "
                           "for higher digital sample rates.",
            })
        elif settings.analog_enabled or settings.mode in (
                "analog", "analog_fast", "analog_all", "analog_continuous",
                "analog_all_continuous"):
            findings.append({
                "level": "info",
                "message": "Analog mode uses RTL analog-only frames. "
                           "High-speed analog captures one selected ADC mux "
                           "channel; maximum analog captures the documented "
                           "physical analog profile.",
            })
        # validation must not touch the hardware: it runs in an API thread,
        # possibly while a capture owns the SPI link, and writing the pattern
        # trigger registers here would also overwrite a running capture's setup
        if self._requires_unavailable_high_rate_deep_path(
                settings, self._build_trigger(settings, configure=False)):
            findings.append({
                "level": "warning",
                "message": "Single-shot deep digital capture is clean up to the "
                           "full 200 MHz sample clock. Live rolling capture has "
                           f"a tested ceiling around {DIGITAL_LIVE_SAMPLE_RATE_HZ / 1_000_000:.0f} MHz "
                           "on this board revision; above that the newest "
                           "samples are kept and overruns are reported. Use "
                           "single-shot for trustworthy high-rate deep capture.",
            })
        return findings

    def _digital_readback_compression(self, settings: CaptureSettings) -> str:
        # Packed capture produces compressed data in SDRAM — readback must
        # be raw to avoid double-compression (RLE on top of packed).
        if settings.packed_mode:
            return "raw"
        if settings.mode in ("single", "continuous", "rolling", "digital_narrow", "triggered"):
            return settings.readback_compression
        return "raw"

    def capture(self, settings: CaptureSettings,
                progress: Optional[ProgressCb] = None,
                stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            dev = self._dev

            dev.set_readback_compression(
                self._digital_readback_compression(settings))
            dev.set_packed_mode(settings.packed_mode)

            # Build trigger (may configure hardware via dev.trigger_decode)
            trigger = self._build_trigger(settings)

            # Validate rate against board limits
            if (self._requires_unavailable_high_rate_deep_path(settings, trigger)
                    and not self._use_rolling_single_shot(settings, trigger)):
                raise HardwareError(
                    "Live rolling capture above ~50 MHz exceeds the tested "
                    "ceiling for this board revision. Use single-shot for "
                    "trustworthy deep capture at any rate up to the full "
                    "200 MHz sample clock, or lower the live rate.")

            # Resolve capture strategy
            strategy = self._strategy_for(settings)
            if strategy is None:
                raise HardwareError(f"No capture strategy for mode: {settings.mode}")

            t0 = time.time()
            self._log(f"capture rate={settings.sample_rate:.0f} "
                      f"nsamp={settings.num_samples} mode={settings.mode}")
            try:
                driver_progress = _adapt_driver_progress(progress)
                result = strategy.capture(dev, settings, trigger=trigger,
                                          progress=driver_progress,
                                          stop_evt=stop_evt)
            except HardwareError:
                self._recover_after_failed_capture()
                raise
            except Exception as e:
                self._last_error = str(e)
                self._recover_after_failed_capture()
                raise HardwareError(f"Capture failed: {e}") from e
            self._timings["last_capture_s"] = time.time() - t0
            # If packed capture mode was active, decode the compressed stream.
            # packed_decode returns (16, N) uint8 bit-planes and raw 12-bit ADC
            # codes; CaptureResult.digital is a 1-D bit-packed uint16 per
            # sample and .analog is volts (see base.py), matching every other
            # capture strategy, so both need converting before they leave here.
            if settings.packed_mode and result.digital is not None:
                total_samples = int(settings.num_samples)
                dig, ana = packed_decode(result.digital, total_samples)
                packed = np.zeros(total_samples, dtype=np.uint16)
                for ch in range(dig.shape[0]):
                    packed |= dig[ch].astype(np.uint16) << ch
                result.digital = packed
                result.analog = {f"a{c[3:]}": adc_to_volts(codes)
                                 for c, codes in ana.items()}
            return result

    def stream_capture(self, settings: CaptureSettings,
                       progress: Optional[ProgressCb] = None,
                       stop_evt: Optional[threading.Event] = None):
        if settings.mode != "digital_narrow":
            raise HardwareError(
                "stream_capture is only implemented for digital_narrow")
        # The adapter lock is held only while the driver advances the stream
        # (and for setup/teardown), never while a chunk is handed to the
        # consumer: holding it for the whole stream blocked every other
        # adapter call (generator start/stop/status, status polls) until the
        # stream ended.
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            dev = self._dev
            from driver.ols_spi_device import (
                narrow_digital_flags,
                unpack_narrow_digital_words,
            )
            channel = settings.enabled_digital[0] if settings.enabled_digital else 0
            divider, actual_rate = self._actual_sample_rate(
                dev, float(settings.sample_rate))
            window_samples = max(1, int(settings.num_samples))
            old_flags = getattr(dev, "_raw_flags", 0)
            dev._raw_flags = (old_flags & ~0x3E000) | narrow_digital_flags(channel)
            dev.set_analog_config(0)
            driver_progress = _adapt_driver_progress(progress)
            stream = dev.stream_ring_capture(
                rate_hz=float(settings.sample_rate),
                window_samples=window_samples,
                stop_evt=stop_evt or threading.Event(),
                progress_cb=driver_progress)
        try:
            while True:
                with self._lock:
                    try:
                        data, total, window_samp, overrun = next(stream)
                    except StopIteration:
                        break
                sample_count = min(
                    len(data) // 2 * 16, window_samp * 16)
                digital = unpack_narrow_digital_words(
                    data, channel=channel,
                    sample_count=sample_count)
                warnings = [
                    f"Packed 1-channel narrow digital mode on d{channel}"]
                if overrun:
                    warnings.append(
                        f"Streaming ring overrun count is {overrun}")
                yield CaptureResult(
                    sample_rate=actual_rate,
                    digital=digital[:window_samples],
                    analog={},
                    divider=divider,
                    warnings=warnings)
        finally:
            with self._lock:
                try:
                    close = getattr(stream, "close", None)
                    if close is not None:
                        close()
                finally:
                    dev._raw_flags = old_flags

    @staticmethod
    def _actual_sample_rate(dev, requested_rate: float) -> tuple[int, float]:
        divider = max(0, round(dev.sample_clk / requested_rate) - 1)
        return divider, float(dev.sample_clk) / float(divider + 1)

    def _use_rolling_single_shot(self, settings: CaptureSettings, trigger) -> bool:
        """Single-shot deep digital must not use the continuous ring path.

        Hardware testing showed the ring is a retention window, not a lossless
        high-rate acquisition path. Keep the Bit Engine waveform helper for
        direct diagnostics/tests, but never select it for user captures.
        """
        return False

    def _requires_unavailable_high_rate_deep_path(
            self, settings: CaptureSettings, trigger) -> bool:
        if settings.analog_enabled:
            return False
        if settings.mode in ("analog", "analog_fast", "analog_all"):
            return False
        if settings.mode == "digital_narrow":
            return False
        if settings.mode not in ("single", "continuous", "rolling"):
            return False
        if trigger is not None or settings.trigger.pre_trigger_samples:
            return False
        if settings.mode in ("continuous", "rolling"):
            # Rolling/continuous is a repeated finite-capture live view here,
            # not the old ring streamer. The 50 MHz ceiling is caused by CDC
            # sampling artifacts when sys_clk-domain signals cross to fast_clk.
            # Packed mode (mso_capture) runs entirely in fast_clk with no CDC
            # crossing — the ceiling does not apply.
            if settings.packed_mode:
                return False
            return settings.sample_rate > DIGITAL_LIVE_SAMPLE_RATE_HZ
        # Single-shot deep SDRAM capture is validated clean at every rate up to
        # the full sample clock (open-page write path + producer-done completion),
        # so it is no longer rate-limited.
        return False

    def _rolling_single_shot_capture(self, dev, *, rate: float, nsamp: int,
                                     progress: Optional[ProgressCb],
                                     stop_evt: Optional[threading.Event]) -> tuple[bytes, int]:
        from driver.spi_protocol import CMD_ABORT_CAPTURE

        div = max(0, round(dev.sample_clk / rate) - 1)
        dev.reset()
        time.sleep(0.02)
        dev.spi.flush()
        dev._write_capture_config(
            div=div, samples=nsamp, delay_count=nsamp,
            mask=0, value=0, flags=dev._raw_flags,
            fast_mode=True, continuous=True)
        dev.set_bitbang_pwm(dev.debug_ch0_enabled)
        dev.spi.flush()
        status = dev.pkt.arm_capture()
        if status < 0:
            return b"", 0

        self._last_rolling_status = {}
        expected_seq = None
        start_sample = 0
        deadline = time.time() + max(3, nsamp // 10000 + 2)
        last_status = {}
        data = b""
        frozen = False
        try:
            while time.time() < deadline:
                if stop_evt and stop_evt.is_set():
                    return b"", 0
                st = dev.pkt.get_status()
                last_status = st
                self._last_rolling_status = dict(st)
                expected_seq = st.get("capture_seq", expected_seq)
                producer = st.get("producer_index")
                oldest = st.get("oldest_index")
                if producer is not None and oldest is not None:
                    available = int(producer) - int(oldest)
                    if progress:
                        progress(min(available, nsamp), nsamp, "capturing")
                    if available >= nsamp:
                        start_sample = max(int(oldest), int(producer) - nsamp)
                        break
                time.sleep(0.002)
            else:
                raise HardwareError(
                    "High-rate rolling capture timed out waiting for producer index")

            # Freeze the continuous writer before indexed readback. Reading the
            # SDRAM ring while the FPGA is still writing can splice live/old
            # regions into one waveform; the symptom is a clean CH0 PWM that
            # turns into a second apparent frequency mid-capture.
            try:
                dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                frozen = True
            except Exception:
                pass
            time.sleep(0.005)

            data = dev.read_capture_range(start_sample, nsamp)[: nsamp * 2]
            if len(data) < nsamp * 2:
                raise HardwareError(
                    f"High-rate rolling capture returned {len(data) // 2} "
                    f"samples for {nsamp} requested")
            if expected_seq is not None:
                dev.ack_capture_done(expected_seq)
            if progress and data:
                progress(len(data) // 2, nsamp, "capturing")
            return data, start_sample
        finally:
            if not frozen:
                try:
                    dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                except Exception:
                    pass
            overrun = last_status.get("overrun_count")
            if overrun:
                self._log(f"rolling_overrun count={overrun}")

    def _repair_rolling_boundary_glitches(
            self, data: bytes, start_sample: int = 0) -> tuple[bytes, int]:
        if len(data) < 6:
            return data, 0
        samples = np.frombuffer(data[:len(data) - (len(data) % 2)],
                                dtype="<u2").copy()
        repaired = 0
        for idx in range(1, len(samples) - 1):
            if ((start_sample + idx) & 0xFF) != 0:
                continue
            prev = int(samples[idx - 1])
            cur = int(samples[idx])
            nxt = int(samples[idx + 1])
            same_neighbors = ~(prev ^ nxt) & 0xFFFF
            glitch_bits = (cur ^ prev) & same_neighbors
            if glitch_bits:
                samples[idx] = (cur & ~glitch_bits) | (prev & glitch_bits)
                repaired += int(glitch_bits.bit_count())
        if repaired == 0:
            return data, 0
        fixed = samples.astype("<u2").tobytes()
        if len(data) % 2:
            fixed += data[-1:]
        return fixed, repaired

    def _build_trigger(self, settings: CaptureSettings, configure: bool = True):
        """Trigger for ``settings``; with ``configure`` False no hardware access."""
        trig = settings.trigger
        dev = self._dev
        if trig.type == "generic_pattern":
            pattern = project_generic_pattern_for_hardware(trig).model_dump()
            if pattern.get("clock_source") == "internal_baud" and dev is not None:
                pattern["baud_div"] = max(1, round(dev.sample_clk / max(1, trig.baud or 115200)))
            if configure:
                dev.configure_pattern_trigger(pattern)
            return None
        if configure:
            dev.configure_pattern_trigger(None)
        register_trigger = to_register_config(trig)
        if register_trigger is not None:
            return register_trigger
        if trig.type == "uart_byte" and trig.value is not None:
            # Configured via trigger_decode just before arm
            if configure:
                dev = self._dev
                ch = trig.channels[0] if trig.channels else 0
                dev.trigger_decode(match_byte=trig.value & 0xFF, channel=ch,
                                   baud=trig.baud or 115200, enable=True)
            return None
        return None

    def _recover_after_failed_capture(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.set_analog_config(0)
            self._dev.reset()
            self._dev.spi.flush()
            self._log("capture_recovery_reset")
        except Exception as e:
            self._last_error = str(e)
        try:
            close = getattr(self._dev, "close", None)
            open_ = getattr(self._dev, "open", None)
            if callable(close) and callable(open_):
                close()
                time.sleep(0.05)
                open_()
                self._reset_after_connect()
                self._log("capture_recovery_reopen")
        except Exception as e:
            self._last_error = str(e)

    # ── generator ────────────────────────────────────────────────────

    def generator_status(self) -> GeneratorStatus:
        busy = False
        live_armed = False
        actual_rate = None
        below_floor = False
        with self._lock:
            if self._dev is not None:
                try:
                    st = self._dev.pkt.get_status()
                    busy = bool(st.get("gen_busy", False))
                except Exception:
                    pass
                # A repeating live generator loops in hardware; the FPGA's
                # gen_busy bit may not be set until the first capture re-kick,
                # so report the armed pattern as running too.
                live_armed = bool(getattr(self._dev, "live_gen_active", False))
                if self._gen_cfg is not None and self._gen_cfg.protocol in (
                        "uart", "rs485", "bitbang"):
                    try:
                        rate = max(1, int(self._gen_cfg.baud))
                        actual_rate = self._dev.actual_symbol_rate(rate)
                        below_floor = self._dev._uart_baud_div(rate) > \
                            self._dev.gen_div_mask
                    except Exception:
                        pass
        running = busy or live_armed
        return GeneratorStatus(busy=running, running=running,
                               protocol=self._gen_cfg.protocol if self._gen_cfg else None,
                               config=self._gen_cfg.model_dump() if self._gen_cfg else None,
                               actual_symbol_rate=actual_rate,
                               below_floor=below_floor,
                               divider_width=getattr(self._dev, "_gen_div_width", 16),
                               supported=True,
                               detail="UART/RS-485/I2C/SPI/SWD/raw Bit Banger generator (FPGA); SWD and SPI use send + capture")

    def generator_configure(self, cfg: GeneratorConfig) -> None:
        if cfg.protocol not in ("uart", "rs485", "i2c", "spi", "swd", "bitbang"):
            raise HardwareError(
                f"Generator protocol '{cfg.protocol}' is not supported by the "
                "current FPGA firmware (supported: uart, rs485, i2c, spi, swd, bitbang)")
        self._gen_cfg = cfg

    def generator_start(self, live: bool = False) -> None:
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            cfg = self._gen_cfg
            if cfg is None:
                raise HardwareError("Generator not configured")
            self._log(f"gen_start {cfg.protocol} live={live}")
            if live:
                self._start_live_generator(self._dev, cfg)
                return
            data = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"
            if cfg.protocol == "uart":
                self._dev.send_uart(data, baud=cfg.baud, tx_pin=cfg.tx_pin)
            elif cfg.protocol == "rs485":
                self._dev.send_rs485(data, baud=cfg.baud,
                                     b_pin=cfg.tx_pin, a_pin=cfg.scl_pin,
                                     repeat=cfg.continuous or cfg.repeat != 1)
            elif cfg.protocol == "i2c":
                self._dev.i2c_read_setup(cfg.i2c_address, cfg.i2c_register,
                                         read_len=cfg.i2c_read_len,
                                         speed=cfg.baud, tx_pin=cfg.tx_pin,
                                         scl_pin=cfg.scl_pin)
                self._dev.start_gen()
            elif cfg.protocol == "spi":
                # The SPI generator only exists on the atomic CMD_GEN_CAPTURE
                # path (it loops MOSI/SCLK straight into the capture stream);
                # there is no free-running send-without-capture mode.
                raise HardwareError(
                    "SPI generator requires 'Send + capture' on this "
                    "firmware; standalone send is not supported")
            elif cfg.protocol == "swd":
                raise HardwareError(
                    "SWD transaction capture requires 'Send + capture' on this firmware")
            elif cfg.protocol == "bitbang":
                from ..generator.bitbang import expand_symbols
                symbols = expand_symbols(cfg.extra, max(1, int(cfg.baud)))
                self._dev.send_raw_symbols(
                    symbols, symbol_rate=max(1, int(cfg.baud)),
                    tx_pin=int(cfg.tx_pin), scl_pin=int(cfg.scl_pin))
            else:
                raise HardwareError(
                    f"Generator protocol '{cfg.protocol}' is not supported")

    def _start_live_generator(self, dev, cfg: GeneratorConfig) -> None:
        """Arm a repeating generator pattern that survives capture resets.

        The pattern loops in hardware and the driver re-kicks it after every
        capture chunk's reset, so a rolling/live capture continuously shows
        the generator output on its pin (the one-shot path used to always
        play in the inter-chunk gap and was never sampled).
        """
        from driver import bit_bang
        from driver.spi_protocol import GEN_FLAG_RS485_PAIR

        if cfg.protocol == "bitbang":
            from ..generator.bitbang import expand_symbols
            symbols = expand_symbols(cfg.extra, max(1, int(cfg.baud)))
            dev.set_live_gen(
                bit_bang.pack_symbols(symbols),
                symbol_rate=max(1, int(cfg.baud)),
                tx_pin=int(cfg.tx_pin), scl_pin=int(cfg.scl_pin))
            return
        if cfg.protocol not in ("uart", "rs485"):
            raise HardwareError(
                f"{cfg.protocol.upper()} generator cannot stream standalone; "
                "use uart, rs485, or bitbang live mode (or Send + capture)")
        data = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"
        data = data[:bit_bang.max_uart_bytes()]
        packed = bit_bang.pack_symbols(bit_bang.uart_symbols(data))
        kwargs = {
            "packed_symbols": packed,
            "symbol_rate": max(1, int(cfg.baud)),
            "tx_pin": int(cfg.tx_pin),
        }
        if cfg.protocol == "rs485":
            kwargs["scl_pin"] = int(cfg.scl_pin)
            kwargs["flags"] = GEN_FLAG_RS485_PAIR
        dev.set_live_gen(**kwargs)

    def generator_stop(self) -> None:
        with self._lock:
            if self._dev is None:
                return
            self._log("gen_stop")
            # Live mode loops in hardware until CMD_GEN_STOP; one-shot sends
            # finish on their own, so only live mode needs the explicit stop.
            self._dev.clear_live_gen()

    def mcp4725_write_and_measure(self, address: int, code: int,
                                  sda_pin: int = 5, scl_pin: int = 4,
                                  adc_channel: int = 3) -> np.ndarray:
        """Write the external DAC, then sample its held analog output.

        The ordinary I2C generator route is a LIS3DH register read and does
        not send ``data_hex``. Use the driver's write-only Bit Engine frame
        explicitly so the two DAC data bytes reach the wired board.
        """
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            if (sda_pin == scl_pin or not 0 <= sda_pin <= 22
                    or not 0 <= scl_pin <= 22):
                raise HardwareError("SDA and SCL must be different external digital pins")
            if adc_channel not in (1, 2, 3, 4):
                raise HardwareError("Analog input must use ADC channel 1, 2, 3, or 4")
            dev = self._dev
            frame = bytes([(address << 1) & 0xFE, (code >> 8) & 0x0F,
                           code & 0xFF])
            physical_channels = {pin for pin in (sda_pin, scl_pin)
                                 if pin < 16}
            spare_channels = [ch for ch in range(16)
                              if ch not in physical_channels]
            _tx_capture_channel, scl_capture_channel, sda_capture_channel = \
                spare_channels[:3]
            dev.set_analog_config(0)
            # Analog capture selects an analog-only raw flag in the driver.
            # Clear that cached mode before the next digital generator
            # capture, or subsequent writes return only a short trace.
            dev.raw_mode(False)
            # SDA on the TX lane is a generator echo. The spare MISO lane is
            # routed from the physical pin, allowing the real ACK slots to be
            # distinguished from the master releasing SDA high.
            # capture_with_gen writes the generator's internal SCL echo and
            # physical SDA sampler to the configured spare capture channels.
            # The user pin numbers are FPGA output selectors, not capture
            # channel numbers; reading those lanes would inspect mapped/raw
            # inputs (or the TX echo) instead of the real bus response.
            scl_channel = scl_capture_channel
            sda_channel = sda_capture_channel
            rising_scl_sda = []
            for _attempt in range(3):
                try:
                    raw = dev.capture_with_gen(
                        rate_hz=1_000_000, nsamples=2048, timeout=6,
                        proto="I2C", i2c_speed=100_000, i2c_frame=frame,
                        i2c_tx_pin=sda_pin, i2c_scl_pin=scl_pin,
                        i2c_read_len=0, fast_mode=True,
                    )
                except HardwareError:
                    raise
                except Exception as exc:
                    raise HardwareError(
                        f"MCP4725 I2C capture failed: {exc}") from exc
                samples_digital = [
                    int.from_bytes(raw[index:index + 2], "little")
                    for index in range(0, len(raw) - 1, 2)]
                rising_scl_sda = []
                for previous, current in zip(samples_digital,
                                             samples_digital[1:]):
                    if (not (previous & (1 << scl_channel))
                            and current & (1 << scl_channel)):
                        rising_scl_sda.append(
                            bool(current & (1 << sda_channel)))
                # A three-byte write has 27 data/ACK clocks plus one STOP
                # cleanup edge. Retry truncated captures, not a real NACK.
                if len(rising_scl_sda) >= 27:
                    break
            ack_indices = (8, 17, 26)
            self._mcp4725_last_acknowledgements = tuple(
                not rising_scl_sda[index] for index in ack_indices
                if index < len(rising_scl_sda))
            self._mcp4725_last_ack_count = len(rising_scl_sda)
            # The DAC retains the volatile output after the I2C STOP. Capture
            # after the write because this bitstream's analog capture uses a
            # separate ADC mode from the atomic digital generator capture.
            try:
                result = self.capture(CaptureSettings(
                    mode="analog_all", analog_enabled=True,
                    sample_rate=24_000, num_samples=64,
                ))
            except HardwareError:
                raise
            except Exception as exc:
                raise HardwareError(
                    f"MCP4725 analog measurement failed: {exc}") from exc
            samples = result.analog.get(f"a{adc_channel}")
            if samples is None or len(samples) == 0:
                raise HardwareError(
                    f"ADC{adc_channel} returned no analog samples")
            return samples

    def mcp4725_last_acknowledgements(self) -> tuple[bool, ...]:
        """Return physical SDA ACK bits captured during the last DAC write."""
        return tuple(getattr(self, "_mcp4725_last_acknowledgements", ()))

    def mcp4725_prepare_fast(self, sda_pin: int, scl_pin: int) -> None:
        """Set up the external I2C RX path for timed DAC writes."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            if (sda_pin == scl_pin or not 0 <= sda_pin <= 22
                    or not 0 <= scl_pin <= 22):
                raise HardwareError("SDA and SCL must be different external digital pins")
            try:
                self._dev.external_i2c_prepare(sda_pin, scl_pin)
            except (RuntimeError, ValueError) as exc:
                raise HardwareError(str(exc)) from exc

    def mcp4725_write_fast(self, address: int, code: int) -> tuple[bool, ...]:
        """Send one DAC code and read ACKs without a full scope capture."""
        with self._lock:
            if self._dev is None:
                raise HardwareError("Device not connected")
            frame = bytes([(address << 1) & 0xFE, (code >> 8) & 0x0F,
                           code & 0xFF])
            try:
                acks = self._dev.external_i2c_write(frame)
            except (RuntimeError, ValueError) as exc:
                raise HardwareError(str(exc)) from exc
            self._mcp4725_last_acknowledgements = tuple(acks)
            return tuple(acks)

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
            self._log(f"gen_capture {cfg.protocol} nsamp={nsamp}")
            # Generator loopback is digital-only; a previous mixed-analog
            # capture leaves the device in MODE_MIXED, which would make the
            # FPGA stream 5-byte mixed frames that stride-4 parsing turns
            # into garbage. Force digital mode first.
            dev.set_analog_config(0)

            cb = _adapt_driver_progress(progress)

            if cfg.protocol == "i2c":
                # On the MAX1000 board, the useful I2C loopback is the real
                # LIS3DH bus: map capture channels onto the sensor pins and
                # let the generator drive that physical bus. Capturing the
                # internal generator echo only proves SCL/SDA toggled; it will
                # always NACK because no slave is on that synthetic bus.
                sda_ch = int(cfg.tx_pin)
                scl_ch = int(cfg.scl_pin)
                sda_pin = int(cfg.extra.get("sda_physical_pin", 24))
                scl_pin = int(cfg.extra.get("scl_physical_pin", 25))
                gen_pin = int(cfg.extra.get("i2c_generator_pin", 31))
                dev.set_pin_map(scl_ch, scl_pin)
                dev.set_pin_map(sda_ch, sda_pin)
                dev.spi.flush()
                time.sleep(0.005)
                dev_w = (int(cfg.i2c_address) << 1) & 0xFE
                dev_r = ((int(cfg.i2c_address) << 1) | 0x01) & 0xFF
                try:
                    raw = dev.capture_with_gen(
                        rate_hz=rate, nsamples=nsamp, timeout=6,
                        proto='I2C', i2c_speed=int(cfg.baud),
                        i2c_frame=bytes([dev_w, int(cfg.i2c_register) & 0xFF]),
                        i2c_tx_pin=gen_pin, i2c_scl_pin=gen_pin,
                        i2c_read_len=int(cfg.i2c_read_len),
                        i2c_dev_r=dev_r, fast_mode=False)
                finally:
                    dev.set_pin_map(scl_ch, scl_ch)
                    dev.set_pin_map(sda_ch, sda_ch)
                    dev.spi.flush()
            elif cfg.protocol == "uart":
                dev._gen_data = data
                dev._gen_baud = cfg.baud
                dev._gen_tx_pin = cfg.tx_pin
                # UART loopback is an internal generator-to-capture check. Use
                # the mapped capture path so d{tx_pin} sees the generated bit
                # stream directly; the raw fast path samples physical pins and
                # can decode shifted bytes after earlier pin-map exercises.
                raw = dev.capture_with_gen(rate_hz=rate, nsamples=nsamp,
                                           stop_evt=stop_evt, progress_cb=cb,
                                           fast_mode=False)
            elif cfg.protocol == "rs485":
                dev._gen_data = data
                dev._gen_baud = cfg.baud
                dev._gen_tx_pin = cfg.tx_pin
                raw = dev.capture_with_gen(rate_hz=rate, nsamples=nsamp,
                                           stop_evt=stop_evt, progress_cb=cb,
                                           proto='RS485',
                                           rs485_b_pin=cfg.tx_pin,
                                           rs485_a_pin=cfg.scl_pin,
                                           rs485_de_pin=cfg.extra.get("de_pin"),
                                           fast_mode=False)
            elif cfg.protocol == "spi":
                # SPI generator loops MOSI/SCLK and optional auxiliary
                # CS/MISO routes into the capture stream directly.
                # cfg.baud is reused here as the SPI clock rate in Hz.
                dev._gen_data = data
                spi_clk_div = max(1, int(dev.sys_clk // (2 * max(1, int(cfg.baud)))))
                raw = dev.capture_with_gen(rate_hz=rate, nsamples=nsamp,
                                           stop_evt=stop_evt, progress_cb=cb,
                                           proto='SPI',
                                           spi_mosi_pin=cfg.tx_pin,
                                           spi_sclk_pin=cfg.scl_pin,
                                           spi_cs_pin=cfg.extra.get("cs_pin"),
                                           spi_miso_pin=cfg.extra.get("miso_pin", 23),
                                           spi_cs_channel=cfg.extra.get("cs_capture_channel"),
                                           spi_miso_channel=int(cfg.extra.get("miso_capture_channel", 15)),
                                           spi_clk_div=spi_clk_div,
                                           fast_mode=False)
            elif cfg.protocol == "swd":
                requests = cfg.extra.get("requests", [])
                if isinstance(requests, str):
                    import json
                    requests = json.loads(requests)
                ops = []
                for request in requests:
                    if not isinstance(request, dict):
                        raise HardwareError("SWD requests must be objects")
                    op = "r" if bool(request.get("read", True)) else "w"
                    ops.append((op, int(bool(request.get("ap", False))),
                                int(request.get("addr", 0)),
                                int(request.get("data", 0))))
                raw = dev.capture_with_gen(
                    rate_hz=rate, nsamples=nsamp,
                    stop_evt=stop_evt, progress_cb=cb,
                    proto="SWD", swd_ops=ops,
                    swd_clk_hz=max(1, int(cfg.baud)),
                    swd_swdio_pin=int(cfg.tx_pin),
                    swd_swclk_pin=int(cfg.scl_pin),
                    swd_connect=bool(cfg.extra.get("jtag_to_swd", True)),
                    fast_mode=False)
            elif cfg.protocol == "bitbang":
                raise HardwareError(
                    "Bit Banger raw mode currently supports standalone send; "
                    "use a protocol-specific loopback capture for verification")
            else:
                raise HardwareError(
                    f"Loopback capture not supported for '{cfg.protocol}' on hardware")
            if not raw:
                raise HardwareError("Generator capture returned no data")
            n2 = len(raw) - (len(raw) % 2)
            digital = np.frombuffer(raw[:n2], dtype="<u2").astype(np.uint16)
            warnings: list = []
            if getattr(dev, "last_gen_capture_error", None) is True:
                warnings.append(
                    "The FPGA generator-capture state machine reported an "
                    "error: the generator burst did not finish within its "
                    "50 ms window (or nothing was loaded), so channels that "
                    "mirror the generator internally may be cut off; channels "
                    "wired to a physical pin are unaffected. Use a shorter "
                    "burst or a faster baud rate.")
            return CaptureResult(sample_rate=rate, digital=digital,
                                 warnings=warnings)

    # ── diagnostics ──────────────────────────────────────────────────

    def get_debug_info(self) -> DebugInfo:
        raw_meta = ""
        raw_status: Dict = {}
        timings = dict(self._timings)
        extra: Dict[str, Any] = {}
        with self._lock:
            if self._dev is not None:
                try:
                    raw_meta = self._dev.get_metadata().hex()
                    raw_status = self._dev.pkt.get_status()
                    child_timings = getattr(self._dev, "_timings", None)
                    if isinstance(child_timings, dict):
                        timings.update(child_timings)
                    extra["readback_codec"] = getattr(self._dev, "_readback_codec",
                                                       lambda: "unknown")()
                    fifo_config = getattr(self._dev, "last_accel_fifo_config", None)
                    fifo_status = getattr(self._dev, "last_accel_fifo_status", None)
                    fifo_stats = getattr(self._dev, "accel_fifo_stats", None)
                    if fifo_config is not None or fifo_status is not None or fifo_stats is not None:
                        extra["accelerometer_fifo"] = {
                            "config": fifo_config,
                            "status": fifo_status,
                            "stats": dict(fifo_stats) if isinstance(fifo_stats, dict) else fifo_stats,
                        }
                except Exception as e:
                    self._last_error = str(e)
        return DebugInfo(raw_metadata=raw_meta, raw_status=raw_status,
                         last_command=self._last_command,
                         last_error=self._last_error,
                         command_log=self._command_log[-50:],
                         timings=timings, extra=extra)

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
                st = self._dev.pkt.get_status()
                ok = "gen_busy" in st and "capture_seq" in st
                checks.append({
                    "name": "generator_control_plane",
                    "passed": ok,
                    "detail": ("generator/capture status fields visible"
                               if ok else str(st)),
                })
            except Exception as e:
                checks.append({"name": "generator_control_plane",
                               "passed": False, "detail": str(e)})
        return {"passed": all(c["passed"] for c in checks), "checks": checks,
                "message": "Hardware self-test complete "
                           "(full loopback coverage now lives in the generator and bit-banger/MIL tests)"}

    def _log(self, cmd: str) -> None:
        self._last_command = cmd
        self._command_log.append({"t": time.time(), "cmd": cmd})
        if len(self._command_log) > 500:
            self._command_log = self._command_log[-250:]
