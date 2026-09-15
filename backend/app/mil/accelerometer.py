"""Asynchronous live reader for the MAX1000's on-board LIS3DH.

The accelerometer shares the FPGA Bit_Engine and FTDI link with normal
captures.  All sensor I/O therefore runs in one worker and is serialized with
the existing ``ExistingHostAdapter`` lock.  API handlers only change state and
return a snapshot; they never wait for a bus transaction.
"""
from __future__ import annotations

import base64
import json
import struct
import threading
import time
from typing import Any, Callable, Literal, Optional

import numpy as np
from pydantic import BaseModel, model_validator

from ..capture.sample_format import WaveformData, payload_to_digital
from ..capture.session import (CaptureSettings, DeviceMetadata, Session,
                               default_digital_channels)
from ..config import APP_VERSION
from ..hardware.base import HardwareError
from ..state import capture_manager
from ..websocket.manager import manager as websocket_manager


LIS3DH_WHO_AM_I = 0x0F
LIS3DH_ID = 0x33
LIS3DH_CTRL_REG1 = 0x20
LIS3DH_CTRL_REG4 = 0x23
LIS3DH_OUT_X_L = 0x28
LIS3DH_ADDRESSES = (0x19, 0x18)

LIS3DH_ODR_VALUES = (1, 10, 25, 50, 100, 200, 400, 1344, 1600, 5376)
LIS3DH_SCALE_VALUES = (2, 4, 8, 16)

# LIS3DH output words are left-justified.  The number of meaningful bits and
# the sensitivity both depend on the operating mode, not only on CTRL_REG4.
# Values are the official mg/digit sensitivities for each full-scale range.
_LIS3DH_WORD_SHIFT = {"normal": 6, "high_resolution": 4, "low_power": 8}
_LIS3DH_SENSITIVITY_MG = {
    "normal": {2: 4, 4: 8, 8: 16, 16: 48},
    "high_resolution": {2: 1, 4: 2, 8: 4, 16: 12},
    "low_power": {2: 16, 4: 32, 8: 64, 16: 192},
}


class AccelerometerConfig(BaseModel):
    """User-selectable LIS3DH settings from the CTRL register map.

    The high-rate CTRL_REG1 codes are mode-dependent: code 8 is 1.60 kHz
    only in low-power mode, while code 9 is 1.344 kHz in normal/high-resolution
    mode and 5.376 kHz in low-power mode.
    """

    output_data_rate_hz: Literal[1, 10, 25, 50, 100, 200, 400, 1344, 1600, 5376] = 10
    full_scale_g: Literal[2, 4, 8, 16] = 2
    operating_mode: Literal["normal", "high_resolution", "low_power"] = "high_resolution"
    bdu: bool = True
    endian: Literal["little", "big"] = "little"
    self_test: Literal["normal", "positive", "negative"] = "normal"
    x_enabled: bool = True
    y_enabled: bool = True
    z_enabled: bool = True

    @model_validator(mode="after")
    def validate_mode_dependent_odr(self) -> "AccelerometerConfig":
        if self.output_data_rate_hz == 1344 and self.operating_mode == "low_power":
            raise ValueError(
                "1.344 kHz ODR is only valid in normal or high-resolution mode")
        if self.output_data_rate_hz in (1600, 5376) and self.operating_mode != "low_power":
            raise ValueError(
                "1.60 and 5.376 kHz ODRs are low-power-only modes")
        return self

# 10 Hz is the lowest useful LIS3DH output data rate and keeps the polling
# worker lightweight while still feeling live in the UI.
DEFAULT_SAMPLE_RATE_HZ = 10.0
# The pose path needs a genuinely oversampled bus waveform.  At the 2 MHz live
# tap and 150 kHz I²C there are more than six capture samples per SCL period,
# which leaves the decoder margin for edge settling without filtering away
# phase-sensitive ACK/STOP edges.
# The LIS3DH's absolute maximum ODR is 5.376 kHz in low-power mode; that mode
# uses a four-slot FIFO burst below so the bus can carry the sensor rate.
LIVE_TRACE_RATE_HZ = 2_000_000.0
LIVE_TRACE_SAMPLES = 4096
LIVE_TRACE_CHANNEL_MASK = np.uint16((1 << 13) | (1 << 14) | (1 << 15))
# The normal/high-resolution path uses a single-frame dialogue at ordinary
# ODRs. High ODR modes use paced four-slot FIFO bursts sized to one sensor
# period, so every decoded frame comes from a sequential FIFO slot.
LIVE_TRACE_READ_LEN = 6
LIVE_TRACE_REPEAT_TRANSACTIONS = 1
LIVE_TRACE_COMPACT_I2C = False
# A failed generator/capture handoff must not hold the live producer behind
# the driver's legacy multi-second timeout. A complete 400 kHz I2C read is
# only a few milliseconds, so 250 ms leaves ample USB/FPGA margin while
# bounding sample age during recovery.
LIVE_TRACE_TIMEOUT_S = 0.25
# A broken/stale FPGA image can acknowledge the stream command while leaving
# the generator idle.  Do not leave the UI looking at a static ring forever;
# switch to the bounded host-burst reader after this startup grace period.
LIVE_STREAM_WATCHDOG_S = 1.0
# The legacy capture path is retained for bitstreams without a working
# repeating-generator attach route. Keep each fallback transaction short
# enough that the model never waits behind a large readback.
LIVE_FALLBACK_TRACE_SAMPLES = 768
# Ring readback can amortise its SDRAM/SPI setup over a larger window because
# the producer is not blocked by the host decoder. Direct FIFO readback has a
# different constraint: the FIFO reader must keep draining while the decoder
# publishes the frames from the previous delivery. Keep the direct delivery
# at one FIFO-sized quantum so the decoder never holds the physical reader
# behind a 32K-sample batch.
LIVE_STREAM_WINDOW_SAMPLES = 32_768
LIVE_DIRECT_STREAM_WINDOW_SAMPLES = 16_384
# Deliver bounded waveform quanta; the browser worker decodes and publishes
# every packet from each ordered quantum immediately.
# Binary waveform frames no longer pay base64/JSON overhead. Keep each frame
# small enough that one WebSocket send cannot monopolize the event loop or
# hold the USB reader behind a coarse presentation batch.
# Publish one ordered FPGA window at a time. This keeps the browser's live
# decoder fed continuously and prevents a large coalesced WebSocket packet
# from creating a consumer pause that can fill the bounded transport queue.
# The worker still decodes every sample and emits every completed sensor packet
# in order; this is only a delivery boundary, not a source/history buffer.
LIVE_DIRECT_WAVEFORM_BATCH_SAMPLES = LIVE_DIRECT_STREAM_WINDOW_SAMPLES
# Do not prefetch pose data. One ring window becomes one decoder delivery, as
# in b1c; only the board's CSS paint is naturally display-frame bounded.
LIVE_STREAM_FETCH_MULTIPLIER = 1
LIVE_HIGH_ODR_READ_LEN = 24
# A compact 24-byte FIFO burst is used only at 5.376 kHz. The 1 MHz capture
# tap still publishes bounded waveform windows, while the decoder publishes
# each complete XYZ frame without waiting for a history buffer.
LIVE_HIGH_ODR_STREAM_WINDOW_SAMPLES = 32_768
LIVE_FALLBACK_REPEAT_COUNT = 1
# A live fallback read is a transport freshness path, not the sensor ODR
# scheduler. Cap the idle gap so a low configured ODR cannot make the UI's
# last-sample age exceed the live target between host bursts.
# The atomic XYZ read is already serialized on the FTDI link; a 10 ms sleep
# after each read needlessly became the dominant part of the live sample age.
# Keep only a 1 ms cooperative yield for the backend worker.
# Keep a small cooperative yield after each host burst.  A zero-delay loop can
# monopolize the Python/FTDI worker when the browser is also polling status,
# causing multi-second freshness stalls despite fast individual reads.
LIVE_FALLBACK_MAX_INTERVAL_S = 0.001
# Waveform persistence is deliberately decoupled from the physical sample
# callback.  Browser live-window requests can rebuild/read the same rolling
# file, so flushing every short fallback burst can otherwise add 50+ ms gaps.
# Five waveform refreshes per second are sufficient for the rolling view while
# keeping its API/LOD work from competing with the physical read path.
LIVE_WAVEFORM_PERSIST_INTERVAL_S = 0.2
# The LIS3DH supports Fast-mode I²C (400 kHz), but the live decoder uses a
# capture-safe 150 kHz bus clock so the 1 MHz tap sees more than six waveform
# points per generated symbol. Use a
# capture-safe 150 kHz bus clock for the ordinary live dialogue: it gives the
# decoder several points per symbol and makes the direct RLE stream transport-
# limited only by real transitions, not by a one-byte record per sample.
FIFO_I2C_SPEED_HZ = 400_000
# A stop/start mode change can leave one I²C transaction completing just as
# the next worker probes WHO_AM_I. Treat that boundary as transient; a real
# missing sensor still fails after the bounded retry window.
LIS3DH_PROBE_ATTEMPTS = 5
LIS3DH_PROBE_RETRY_S = 0.05
LIVE_TRACE_I2C_SPEED_HZ = 150_000
# The flashed capture image exposes the SDRAM ring cursors in the physical
# four-million-sample address domain. Keep source-age arithmetic in that same
# domain so a producer/source pair straddling the physical wrap is still a
# small, real backlog rather than a 32-bit near-wrap value.
LIVE_TRACE_RING_SAMPLES = 4_194_304
LIVE_TRACE_RING_MASK = LIVE_TRACE_RING_SAMPLES - 1
LIVE_TRACE_RING_HALF = LIVE_TRACE_RING_SAMPLES // 2


def _live_i2c_schedule(output_data_rate_hz: float) -> dict[str, Any]:
    """Select a bounded FIFO dialogue that only consumes fresh LIS3DH data.

    The generator is open-loop, so an unpaced repeated read can drain the
    sensor FIFO before the next sample exists.  Use a transaction that fits in
    the 1,024-symbol generator FIFO and repeat it at a rate matched to the
    number of XYZ slots read.  The low-rate path intentionally retains the
    established single-register dialogue; its output register remains stable
    between reads and does not need a FIFO burst.
    """
    odr = int(round(float(output_data_rate_hz)))
    if odr == 1344:
        return {
            # One FIFO slot per transaction keeps the padded repeat pattern
            # below the FPGA generator FIFO limit while matching the
            # datasheet's 1.344 kHz HR/normal ODR exactly.  A four-slot
            # pattern would need more than 2,000 symbols once paced.
            "read_len": 6,
            "i2c_speed": 400_000,
            "compact_i2c": True,
            "repeat_rate_hz": 1_344.0,
        }
    if odr == 1600:
        return {
            "read_len": 12,
            "i2c_speed": 400_000,
            "compact_i2c": True,
            # Two FIFO slots per transaction at the datasheet's 1.600 kHz
            # ODR require an 800 Hz transaction cadence.
            "repeat_rate_hz": 800.0,
        }
    if odr == 5376:
        return {
            # At 5.376 kHz the 4-slot burst is intentionally unpaced.  Its
            # compact 400 kHz wire time is shorter than the four-sample ODR
            # interval, so it can drain the sensor FIFO without exceeding
            # the 1,024-symbol generator FIFO.
            "read_len": 24,
            "i2c_speed": 400_000,
            "compact_i2c": True,
            "repeat_rate_hz": None,
        }
    return {
        "read_len": LIVE_TRACE_READ_LEN,
        "i2c_speed": LIVE_TRACE_I2C_SPEED_HZ,
        "compact_i2c": LIVE_TRACE_COMPACT_I2C,
        "repeat_rate_hz": None,
    }


def _live_window_samples(settings: CaptureSettings) -> int:
    """Convert the requested UI duration into live-trace sample points."""
    requested_duration_s = (
        max(0, int(settings.num_samples))
        / max(float(settings.sample_rate), 1.0))
    return max(1, min(
        int(round(requested_duration_s * LIVE_TRACE_RATE_HZ)),
        LIVE_TRACE_SAMPLES * 256))


def _live_ring_distance(newer: int, older: int) -> int:
    return (int(newer) - int(older)) & LIVE_TRACE_RING_MASK


def _live_ring_age(newer: int, older: int) -> int:
    """Return source age, treating a source-ahead sample as zero age."""
    distance = _live_ring_distance(newer, older)
    return distance if distance <= LIVE_TRACE_RING_HALF else 0


def _config_registers(config: AccelerometerConfig) -> tuple[int, int]:
    """Encode the public configuration into LIS3DH CTRL_REG1/4 values."""
    odr_code = {
        1: 0x1, 10: 0x2, 25: 0x3, 50: 0x4, 100: 0x5,
        200: 0x6, 400: 0x7, 1344: 0x9, 1600: 0x8, 5376: 0x9,
    }[config.output_data_rate_hz]
    axes = ((0x01 if config.x_enabled else 0)
            | (0x02 if config.y_enabled else 0)
            | (0x04 if config.z_enabled else 0))
    low_power = config.operating_mode == "low_power"
    ctrl1 = (odr_code << 4) | (0x08 if low_power else 0) | axes

    scale_code = {2: 0x0, 4: 0x1, 8: 0x2, 16: 0x3}[config.full_scale_g]
    self_test_code = {"normal": 0x0, "positive": 0x2, "negative": 0x4}[config.self_test]
    ctrl4 = ((0x80 if config.bdu else 0)
             | (0x40 if config.endian == "big" else 0)
             | (scale_code << 4)
             | (0x08 if config.operating_mode == "high_resolution" else 0)
             | self_test_code)
    return ctrl1, ctrl4


def _lis3dh_word_padding_mask(config: AccelerometerConfig) -> int:
    """Return the unused low bits in one left-justified LIS3DH word."""
    return (1 << _LIS3DH_WORD_SHIFT[config.operating_mode]) - 1


def clean_live_trace(digital: np.ndarray, majority: bool = True) -> np.ndarray:
    """Keep the mirrored bus lines and reject isolated sampled glitches.

    The attach mirror is intentionally exposed only on CH13/CH14/CH15.  The
    capture engine can retain stale values on unrelated channels after a
    previous capture, and a fast sample clock can produce a one-sample
    metastability spike on an I2C edge.  Three-sample majority filtering is
    safe when the capture clock oversamples the generated symbols.  The live
    ring can deliberately run below the symbol rate to keep its source head
    close to the reader, so that path may disable the non-causal filter.
    """
    values = np.asarray(digital, dtype=np.uint16).copy()
    if values.size == 0:
        return values
    values &= LIVE_TRACE_CHANNEL_MASK
    if not majority or values.size < 3:
        return values
    middle = values[1:-1]
    left = values[:-2]
    right = values[2:]
    middle[:] = (left & middle) | (left & right) | (middle & right)
    return values


class _LiveI2CFrameDecoder:
    """Incremental decoder for arbitrary slices of the attach-mirror ring.

    A ring read almost never ends on an I2C STOP edge.  Keeping the current
    transaction, bit group, and SCL plateau across calls prevents the live
    path from dropping every frame whose read window straddles two chunks.
    The bounded transaction budget makes a damaged/noisy mirror recover on its
    own instead of retaining unbounded waveform state.
    """

    MAX_CARRY_SAMPLES = 2048  # enough for one 24-byte FIFO transaction

    def __init__(self, address: int, word_padding_mask: int | None = None,
                 config: AccelerometerConfig | None = None,
                 clock: Callable[[], float] = time.time,
                 read_len: int = 6):
        address = int(address) & 0x7F
        read_len = int(read_len)
        if read_len < 6 or read_len % 6:
            raise ValueError("read_len must be a positive multiple of 6")
        self.expected_write = (address << 1) & 0xFE
        self.expected_read = self.expected_write | 1
        self.expected_register = LIS3DH_OUT_X_L | 0x80
        self.read_len = read_len
        self.decode_config = config or AccelerometerConfig()
        self.word_padding_mask = int(
            _lis3dh_word_padding_mask(self.decode_config)
            if word_padding_mask is None else word_padding_mask) & 0xFFFF
        self._clock = clock
        self._previous: tuple[int, int] | None = None
        self._await_clean_start = False
        self._reset_transaction()

    def reset(self) -> None:
        """Discard a partial transaction, preserving no stale decoder state."""
        # An overrun can leave the next chunk in the middle of a transaction.
        # Ignore the remainder until a clean STOP has been observed, so a
        # repeated START from that partial transaction cannot be mistaken for
        # a fresh frame.
        self._await_clean_start = True
        self._previous = None
        self._reset_transaction()

    def _reset_transaction(self) -> None:
        self._in_txn = False
        self._phase = "idle"
        self._bits = []
        self._data = []
        self._acks = []
        self._data_positions = []
        self._data_times = []
        self._frame_emitted = False
        self._plateau_runs: list[tuple[int, int]] = []
        self._plateau_control = False
        self._txn_samples = 0

    def _start_transaction(self) -> None:
        # The generator uses a repeated START between the register write and
        # the data read. Preserve and validate the write phase before allowing
        # the following bytes to become an XYZ frame. Previously any repeated
        # START discarded the prefix and the decoder only checked that the
        # first read byte looked like *either* possible LIS3DH address.
        if self._in_txn:
            if (self._phase == "write"
                    and self._data == [self.expected_write,
                                       self.expected_register]
                    and self._acks == [0, 0]):
                self._phase = "read"
                self._bits = []
                self._data = []
                self._acks = []
                self._data_positions = []
                self._data_times = []
                self._frame_emitted = False
                self._plateau_runs = []
                self._plateau_control = True
                self._txn_samples = 0
                return
        self._in_txn = True
        self._phase = "write"
        self._bits = []
        self._data = []
        self._acks = []
        self._data_positions = []
        self._data_times = []
        self._frame_emitted = False
        self._plateau_runs = []
        self._plateau_control = True
        self._txn_samples = 0

    def _append_plateau(self, sda: int, count: int = 1) -> None:
        """Record a stable SDA run without expanding oversampled points."""
        if count <= 0:
            return
        if self._plateau_runs and self._plateau_runs[-1][0] == sda:
            level, length = self._plateau_runs[-1]
            self._plateau_runs[-1] = (level, length + count)
        else:
            self._plateau_runs.append((sda, count))

    def _plateau_center(self) -> int:
        """Return the same temporal midpoint as the old expanded list."""
        if len(self._plateau_runs) == 1:
            return self._plateau_runs[0][0]
        target = sum(length for _level, length in self._plateau_runs) // 2
        for level, length in self._plateau_runs:
            if target < length:
                return level
            target -= length
        return 0

    def _finish_byte(
            self, frames: list[tuple[float, float, float]],
            frame_positions: list[int] | None = None,
            sample_index: int | None = None,
            frame_times: list[float] | None = None) -> None:
        if len(self._bits) < 9:
            return
        value = 0
        for bit in self._bits[:8]:
            value = (value << 1) | bit
        self._data.append(value)
        self._acks.append(self._bits[8])
        if frame_positions is not None:
            self._data_positions.append(sample_index)
        if frame_times is not None:
            self._data_times.append(float(self._clock()))
        self._bits = []
        # A complete read is publishable before STOP. This is essential for a
        # chunk ending mid-transaction and prevents duplicate publication on
        # the next chunk when the trailing STOP is eventually observed.
        if (not self._frame_emitted and self._phase == "read"
                and len(self._data) == self.read_len + 1
                and self._data[0] == self.expected_read
                # The read address and every byte except the final one are
                # ACKed; the master NACKs the final byte to terminate the
                # read. A FIFO burst therefore has one address ACK, 23 data
                # ACKs, and one final NACK.
                and self._acks == [0] * self.read_len + [1]):
            payload = bytes(self._data[1:])
            if any(int.from_bytes(
                       payload[offset:offset + 2], self.decode_config.endian)
                   & self.word_padding_mask
                   for offset in range(0, self.read_len, 2)):
                # A misaligned ring window can still contain a complete
                # address/ACK-shaped sequence.  LIS3DH output words are
                # left-justified, so non-zero padding is proof that this is
                # not a sensor sample. Mark the frame consumed and wait for
                # the next repeated transaction instead of publishing it.
                self._frame_emitted = True
                return
            for offset in range(0, self.read_len, 6):
                frames.append(AccelerometerService._decode_xyz(
                    payload[offset:offset + 6], self.decode_config))
                byte_index = 1 + offset + 5
                position = (
                    self._data_positions[byte_index]
                    if frame_positions is not None else None)
                if frame_positions is not None and position is not None:
                    frame_positions.append(position)
                if frame_times is not None:
                    # This is the completion/validation seam, before the
                    # gate, service publication, WebSocket hop, or browser
                    # paint. Use each frame's final byte instead of making a
                    # FIFO burst appear as four simultaneous samples.
                    frame_times.append(self._data_times[byte_index])
            self._frame_emitted = True

    def _feed(self, digital: np.ndarray,
              start_sample: int | None = None,
              collect_times: bool = False):
        values = np.asarray(digital, dtype=np.uint16).reshape(-1)
        if values.size == 0:
            return []
        frames: list[tuple[float, float, float]] = []
        frame_positions: list[int] = []
        frame_times: list[float] = []
        # The capture clock oversamples each generated I2C plateau many
        # times. Walking every sample in Python made the decoder itself the
        # consumer bottleneck at high ODR: the direct FIFO then filled while
        # the host was still visiting identical values. Reduce the waveform
        # to runs of identical SCL/SDA levels and account for each run's
        # length. This preserves the same plateau median and transition
        # positions while making the hot path proportional to bus edges.
        levels = ((values >> 13) & 0x03).astype(np.uint8, copy=False)
        starts = np.r_[0, np.flatnonzero(levels[1:] != levels[:-1]) + 1]
        ends = np.r_[starts[1:], levels.size]
        for run_start, run_end in zip(starts, ends):
            level = int(levels[run_start])
            scl = (level >> 1) & 1
            sda = level & 1
            count = int(run_end - run_start)
            if self._previous is None:
                self._previous = (scl, sda)
                count -= 1
                if count <= 0:
                    continue
                previous_scl, previous_sda = scl, sda
                transition = False
                offset = int(run_start) + 1
            else:
                previous_scl, previous_sda = self._previous
                transition = (scl, sda) != self._previous
                offset = int(run_start)

            if self._await_clean_start:
                if transition and scl and not previous_sda and sda:
                    self._await_clean_start = False
                    self._previous = (scl, sda)
                else:
                    self._previous = (scl, sda)
                continue

            if self._in_txn:
                self._txn_samples += count
                if self._txn_samples > self.MAX_CARRY_SAMPLES:
                    self._reset_transaction()

            if scl:
                # START/STOP are SDA transitions while SCL is high. A
                # control plateau must not be counted as a data bit. The
                # slave may change SDA on the rising edge of an ACK/NACK
                # after the master released it during SCL-low. That ninth
                # bit is not a START/STOP, even though it is an SDA edge
                # while SCL is high; collect it as the ACK plateau first.
                if transition:
                    ack_edge = (
                        self._in_txn and not self._plateau_control
                        and len(self._bits) == 8 and previous_scl == 0)
                    if ack_edge:
                        self._append_plateau(sda, count)
                    elif previous_scl and previous_sda and not sda:
                        self._start_transaction()
                    elif previous_scl and not previous_sda and sda:
                        if self._in_txn:
                            self._reset_transaction()
                        else:
                            self._plateau_runs = []
                            self._plateau_control = True
                    elif self._in_txn and not self._plateau_control:
                        self._append_plateau(sda, count)
                    elif self._in_txn and previous_scl == 0:
                        self._append_plateau(sda, count)
                elif self._in_txn and not self._plateau_control:
                    self._append_plateau(sda, count)
            elif transition and previous_scl:
                if self._in_txn and not self._plateau_control:
                    if self._plateau_runs:
                        self._bits.append(self._plateau_center())
                        self._finish_byte(
                            frames,
                            frame_positions if start_sample is not None else None,
                            (int(start_sample) + offset
                             if start_sample is not None else None),
                            frame_times if collect_times else None)
                self._plateau_runs = []
                self._plateau_control = False
            self._previous = (scl, sda)
        if start_sample is None:
            if collect_times:
                return list(zip(frames, frame_times))
            return frames
        if collect_times:
            return list(zip(frame_positions, frames, frame_times))
        return list(zip(frame_positions, frames))

    def feed(self, digital: np.ndarray) -> list[tuple[float, float, float]]:
        return self._feed(digital)

    def feed_indexed(self, digital: np.ndarray,
                     start_sample: int) -> list[
                         tuple[int, tuple[float, float, float]]]:
        """Decode frames and retain each frame's source-ring completion index."""
        return self._feed(digital, int(start_sample))

    def feed_timed(self, digital: np.ndarray) -> list[
            tuple[tuple[float, float, float], float]]:
        """Decode frames with the wall-clock completion timestamp."""
        return self._feed(digital, collect_times=True)

    def feed_indexed_timed(self, digital: np.ndarray,
                           start_sample: int) -> list[
                               tuple[int, tuple[float, float, float], float]]:
        """Decode indexed frames and timestamp their validation seam."""
        return self._feed(digital, int(start_sample), collect_times=True)


class _LiveI2CFrameGate:
    """Reject one-frame physical impossibilities without flattening motion."""

    MAX_AXIS_DELTA_G = 0.2

    def __init__(self):
        self._accepted: tuple[Any, tuple[float, float, float]] | None = None
        self._candidate: tuple[Any, tuple[float, float, float]] | None = None

    def reset(self) -> None:
        self._accepted = None
        self._candidate = None

    @classmethod
    def _near(cls, left: tuple[float, float, float],
              right: tuple[float, float, float]) -> bool:
        return max(abs(a - b) for a, b in zip(left, right)) <= cls.MAX_AXIS_DELTA_G

    def feed_indexed(self, items: list[
            tuple[Any, tuple[float, float, float]]]) -> list[
                tuple[Any, tuple[float, float, float]]]:
        """Gate samples while preserving source metadata for timestamps."""
        published: list[tuple[Any, tuple[float, float, float]]] = []
        for item in items:
            sample = item[1]
            if self._candidate is None:
                self._candidate = item
                continue
            if self._accepted is None:
                self._accepted = self._candidate
                published.append(self._candidate)
                self._candidate = item
                continue
            if (not self._near(self._candidate[1], self._accepted[1])
                    and self._near(sample, self._accepted[1])):
                # The candidate is an isolated excursion; replace it with the
                # confirmed return value and keep the one-sample latency.
                self._candidate = item
                continue
            self._accepted = self._candidate
            published.append(self._candidate)
            self._candidate = item
        return published

    def accept_indexed_immediately(self, items: list[
            tuple[Any, tuple[float, float, float]]]) -> list[
                tuple[Any, tuple[float, float, float]]]:
        """Accept protocol-valid points without a look-ahead delay.

        ``feed_indexed`` remains available for offline/de-noising callers,
        but it deliberately holds one candidate to reject an isolated
        excursion. That look-ahead is not allowed on the live pose path:
        frame address/ACK/padding validation has already completed, and a
        presentation target must move on the same point as the decoder.
        """
        if not items:
            return []
        self._accepted = items[-1]
        self._candidate = None
        return list(items)

    def feed(self, samples: list[tuple[float, float, float]]) -> list[
            tuple[float, float, float]]:
        return [sample for _metadata, sample in self.feed_indexed(
            [(None, sample) for sample in samples])]

    def flush_indexed(self) -> list[tuple[Any, tuple[float, float, float]]]:
        """Release a validated tail unless it is an isolated implausible spike.

        Tail-follow transport deliberately skips the ring region between
        batches.  Without a flush, the gate's one-sample confirmation delay
        would silently discard the final decoded frame of every batch.
        """
        if self._candidate is None:
            return []
        candidate = self._candidate
        self._candidate = None
        if self._accepted is None or self._near(candidate[1], self._accepted[1]):
            return [candidate]
        return []

    def flush(self) -> list[tuple[float, float, float]]:
        return [sample for _metadata, sample in self.flush_indexed()]


class AccelerometerStatus(BaseModel):
    running: bool = False
    available: bool = False
    # ``sample_rate_hz`` is retained as the requested sensor ODR for API
    # compatibility.  It is not a measurement of host read throughput.
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ
    # The ODR read back from CTRL_REG1 (or the applied request when the
    # adapter cannot expose register reads).  This is the sensor's configured
    # output rate, not the rate at which the host can fetch samples.
    effective_odr_hz: float = 0.0
    # Completed six-byte XYZ transactions measured at the host boundary.
    # These can be substantially lower than the sensor ODR when USB/I2C
    # latency or a live trace capture limits polling throughput.
    host_read_rate_hz: float = 0.0
    host_data_rate_bytes_per_s: float = 0.0
    # Deprecated aliases retained for older clients.
    measured_rate_hz: float = 0.0
    data_rate_bytes_per_s: float = 0.0
    sample: Optional[dict[str, float]] = None
    last_sample_at: Optional[float] = None
    samples_read: int = 0
    last_error: Optional[str] = None
    effective_config: Optional[dict[str, Any]] = None
    register_readback: Optional[dict[str, int]] = None
    last_sample_sequence: int = 0
    source_sample_index: Optional[int] = None
    # Samples still waiting to be transferred from the FPGA ring after the
    # decoded transport window. This is the queue-health metric, not the age
    # of the point within that window.
    source_backlog_samples: int = 0
    # Raw source samples between the producer and the decoded point. A point
    # can have non-zero age while the transport backlog is zero.
    source_age_samples: int = 0
    capture_to_publish_latency_ms: float = 0.0
    decoded_to_publish_latency_ms: float = 0.0
    live_timing: Optional[dict[str, Any]] = None


class AccelerometerService:
    """Own one cancellable LIS3DH polling worker."""

    def __init__(
        self,
        manager: Any = capture_manager,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.manager = manager
        self.sample_rate_hz = float(sample_rate_hz)
        self._config = AccelerometerConfig()
        self._clock = clock
        self._lock = threading.RLock()
        # Polling and live capture share the adapter's Bit_Engine.  The
        # condition closes the small race between the polling worker's
        # live-owner check and its next FIFO/XYZ transaction.
        self._poll_condition = threading.Condition(self._lock)
        self._polling_io = False
        self._live_starting = False
        self._stop_evt: Optional[threading.Event] = None
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        self._availability_known = False
        self._status = AccelerometerStatus(sample_rate_hz=self.sample_rate_hz)
        self._live_session: Optional[Session] = None
        self._live_store: Any = None
        self._live_settings: Optional[CaptureSettings] = None
        self._live_stop_evt: Optional[threading.Event] = None
        self._live_worker: Optional[threading.Thread] = None
        self._live_persist_stop_evt: Optional[threading.Event] = None
        self._live_persist_evt: Optional[threading.Event] = None
        self._live_persist_worker: Optional[threading.Thread] = None
        # When the ring transport is available, ordinary tracking is fed by
        # the same validated FPGA ring producer used by Live capture.  Keep
        # this explicit so a rolling session can attach to the existing owner
        # instead of launching a second generator on the shared Bit_Engine.
        self._continuous_stream_active = False
        self._continuous_stream_watchdog = False
        # Transport provenance is deliberately separate from decoded pose
        # status. A valid later sample must not erase evidence that the live
        # source fell back from direct FIFO to atomic reads.
        self._live_transport: dict[str, Any] | None = None
        self._live_attached_to_tracking = False
        self._live_capture_accepting = False
        self._live_digital = np.zeros(0, dtype=np.uint16)
        self._live_persist_dirty = False
        self._live_dialogue_setup_ready = False
        self._accelerometer_address: Optional[int] = None
        self._last_fifo_error: str | None = None
        self._rate_window_started: Optional[float] = None
        self._rate_window_samples = 0
        self._sample_sequence = 0
        # CaptureManager uses this back-reference to route a normal Live
        # request to the already-running sensor service.  It also keeps
        # independently-created services (the test seam) isolated from the
        # process-global service.
        try:
            setattr(self.manager, "_accelerometer_service", self)
        except Exception:
            pass

    def config(self) -> dict:
        """Return the pending LIS3DH configuration."""
        with self._lock:
            return self._config.model_dump()

    def update_config(self, config: AccelerometerConfig | dict) -> dict:
        """Set pending LIS3DH configuration while the stream is stopped.

        Configuration is deliberately applied at the beginning of the next
        tracking run.  This keeps register writes out of the live polling
        thread and prevents changing the sensor while a capture owns the
        shared FPGA/FTDI link.
        """
        next_config = (config if isinstance(config, AccelerometerConfig)
                       else AccelerometerConfig.model_validate(config))
        with self._lock:
            if self._status.running or self._live_session is not None:
                raise HardwareError("Stop accelerometer tracking before changing configuration")
            if self._worker is not None and self._worker.is_alive():
                raise HardwareError(
                    "Wait for accelerometer tracking to finish stopping before changing configuration")
            self._config = next_config
            self.sample_rate_hz = float(next_config.output_data_rate_hz)
            self._status.sample_rate_hz = float(next_config.output_data_rate_hz)
            return self._config.model_dump()

    def status(self) -> dict:
        with self._lock:
            status = self._status.model_copy(deep=True)
            # FIFO transport diagnostics are read from the already-cached
            # host-driver state. Do not issue another I2C transaction from a
            # status poll while the high-ODR worker owns the bus.
            raw = getattr(getattr(self.manager, "device", None), "_dev", None)
            fifo_status = getattr(raw, "last_accel_fifo_status", None)
            fifo_stats = getattr(raw, "accel_fifo_stats", None)
            if isinstance(fifo_status, dict) or isinstance(fifo_stats, dict):
                timing = dict(status.live_timing or {})
                fifo_diag = {
                    "status": dict(fifo_status)
                    if isinstance(fifo_status, dict) else None,
                    "stats": dict(fifo_stats)
                    if isinstance(fifo_stats, dict) else None,
                }
                if self._last_fifo_error:
                    fifo_diag["error"] = self._last_fifo_error
                timing["fifo"] = fifo_diag
                status.live_timing = timing
            if self._live_transport is not None:
                timing = dict(status.live_timing or {})
                timing["transport"] = dict(self._live_transport)
                status.live_timing = timing
            # The UI uses availability to enable Start.  Being connected to
            # the real adapter is enough to make the asynchronous probe
            # eligible; the worker will clear it if no LIS3DH answers.
            has_hardware = self._has_real_hardware()
            if not has_hardware:
                self._availability_known = False
                status.available = False
            elif not status.running and not self._availability_known:
                status.available = True
            return status.model_dump()

    def start(self) -> dict:
        """Schedule sensor setup and polling, returning immediately."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                # A running stream is idempotent.  A recently stopped stream
                # is still winding down and must not race a new bus owner.
                if self._status.running:
                    return self.status()
                self._status.last_error = "Accelerometer is still stopping"
                return self.status()

            if not self._has_real_hardware():
                self._status.running = False
                self._status.available = False
                self._status.last_error = "No connected real hardware"
                return self.status()

            if self.manager.capture_state in ("capturing", "armed"):
                self._status.running = False
                self._status.available = False
                self._status.last_error = "Cannot start accelerometer while a capture is running"
                return self.status()

            reserve = getattr(self.manager, "reserve_accelerometer", None)
            if callable(reserve) and not reserve():
                self._status.running = False
                self._status.available = True
                self._status.last_error = "Hardware is busy"
                return self.status()

            self._generation += 1
            generation = self._generation
            self._availability_known = True
            self._stop_evt = threading.Event()
            self._status = AccelerometerStatus(
                running=True, available=False,
                sample_rate_hz=float(self._config.output_data_rate_hz),
                sample=None, samples_read=0, last_error=None,
                effective_config=None, register_readback=None)
            self._rate_window_started = None
            self._rate_window_samples = 0
            self._sample_sequence = 0
            self._continuous_stream_watchdog = False
            try:
                self._worker = threading.Thread(
                    target=self._run, args=(generation, self._stop_evt),
                    daemon=True, name="lis3dh-accelerometer")
                self._worker.start()
            except Exception:
                release = getattr(self.manager, "release_accelerometer", None)
                if callable(release):
                    release()
                self._worker = None
                self._stop_evt = None
                self._status.running = False
                self._status.available = True
                self._status.last_error = "Unable to start accelerometer worker"
            return self.status()

    def stop(self, wait_timeout: float = 0.0) -> dict:
        """Request cancellation, optionally waiting briefly during teardown."""
        self.stop_live_capture()
        with self._lock:
            evt = self._stop_evt
            worker = self._worker
            if evt is not None:
                evt.set()
            self._status.running = False
        if (wait_timeout > 0 and worker is not None
                and worker is not threading.current_thread()):
            worker.join(timeout=wait_timeout)
        return self.status()

    def start_live_capture(self, settings: CaptureSettings,
                           name: str = "", store: Any = None) -> Session:
        """Create a rolling session fed by real LIS3DH bus captures.

        The tracking worker remains the sole owner of the FTDI/Bit_Engine
        link.  At high ODRs it is already decoding the continuous FPGA ring;
        in that case this session attaches to that producer.  On older
        adapters the dedicated live producer owns the link instead.  In both
        cases the raw dialogue is converted at the boundary to the normal
        packed uint16 waveform format (SDA/SCL/SDO are CH13/14/15).
        """
        with self._lock:
            if not self._status.running:
                raise HardwareError("Accelerometer is not running")
            if self._live_session is not None:
                raise HardwareError("An accelerometer live capture is already running")
            if store is None:
                from ..state import store as default_store
                store = default_store
            live_window_samples = _live_window_samples(settings)
            live_settings = settings.model_copy(update={
                "mode": "rolling",
                "sample_rate": LIVE_TRACE_RATE_HZ,
                "analog_enabled": False,
                "enabled_digital": [13, 14, 15],
                # The live bus waveform has its own fixed trace rate. Keep
                # the duration selected in Capture setup when converting the
                # UI's sample count to that rate.
                "num_samples": live_window_samples,
            })
            raw = getattr(getattr(self.manager, "device", None), "_dev", None)
            device = getattr(self.manager, "device", None)
            metadata = None
            get_metadata = getattr(device, "get_metadata", None)
            if callable(get_metadata):
                try:
                    metadata = get_metadata()
                except Exception:
                    metadata = None
            if not isinstance(metadata, DeviceMetadata):
                metadata = DeviceMetadata(
                    driver="ols_spi", device_name="MAX1000 OLS Logic Analyzer",
                    connection="FTDI FT2232H MPSSE SPI", mock=False,
                    sys_clk_hz=float(getattr(raw, "sys_clk", 0) or 0),
                    sample_clk_hz=float(getattr(raw, "sample_clk", 0) or 0),
                )
            session = Session(
                name=name or "Accelerometer live",
                app_version=APP_VERSION,
                device=metadata,
                settings=live_settings,
                sample_rate=LIVE_TRACE_RATE_HZ,
                sample_clk_hz=metadata.sample_clk_hz,
                num_samples=0,
                channels=default_digital_channels(16),
                tags=["live", "hardware", "accelerometer"],
                notes="Live LIS3DH I2C dialogue (attach mirror on CH13/CH14/CH15)",
            )
            for channel in session.channels:
                channel.enabled = channel.id in {"d13", "d14", "d15"}
            session.channels[13].name = "SEN_SDI"
            session.channels[14].name = "SEN_SPC"
            session.channels[15].name = "SEN_SDO"
            store.save(session)
            store.save_waveform(session.id,
                                WaveformData(sample_rate=LIVE_TRACE_RATE_HZ,
                                             digital=np.zeros(0, dtype=np.uint16)))
            # A running continuous tracking producer already owns the
            # adapter. Attach the session to it rather than starting a second
            # generator that would abort the first one.
            attach_to_tracking = self._continuous_stream_active
            self._live_starting = not attach_to_tracking
            try:
                self._live_session = session
                self._live_store = store
                self._live_settings = live_settings
                self._live_capture_accepting = True
                self._live_digital = np.zeros(0, dtype=np.uint16)
                self._live_persist_dirty = False
                self._live_dialogue_setup_ready = False
                if attach_to_tracking:
                    self._live_attached_to_tracking = True
                    if self._continuous_stream_watchdog:
                        session.diagnostics.append({
                            "level": "warning",
                            "message": (
                                "Accelerometer stream produced no validated bus frames; "
                                "using bounded host-burst fallback"),
                            "ts": self._clock(),
                        })
                    # The ring producer may have started before this session
                    # existed, so start persistence at attachment time.
                    self._start_live_persister()
                else:
                    # The polling worker checks ``_live_session`` before each
                    # read, but that check alone cannot stop a read already in
                    # flight. An XYZ/FIFO read rewrites the Bit_Engine and
                    # sends CMD_ABORT_CAPTURE on the shared adapter, which
                    # would kill the stream immediately after this method
                    # starts it. Publish ownership without waiting on the
                    # adapter: the producer acquires adapter._lock before its
                    # first hardware operation, so any in-flight poll
                    # finishes before the stream touches the Bit_Engine.
                    address = self._accelerometer_address
                    if address is None:
                        raise HardwareError("LIS3DH address is not ready")
                    # Do not launch the ring owner into the adapter lock while
                    # a polling FIFO transaction is already in flight. The
                    # FIFO path can legitimately take several USB round trips;
                    # making the stream wait here gives that operation a
                    # bounded, observable handoff instead of leaving the
                    # producer blocked behind a stale poll while the browser
                    # sees an empty live session.
                    handoff_deadline = time.monotonic() + 1.0
                    while self._polling_io and time.monotonic() < handoff_deadline:
                        self._poll_condition.wait(
                            timeout=max(0.001,
                                       handoff_deadline - time.monotonic()))
                    live_stop_evt = threading.Event()
                    self._live_stop_evt = live_stop_evt
                    try:
                        self._live_worker = threading.Thread(
                            target=self._run_live_producer,
                            args=(device, raw, address, live_stop_evt, session,
                                  self._live_decoder_config()),
                            daemon=True, name="lis3dh-live-capture")
                        self._live_worker.start()
                    except Exception:
                        self._live_session = None
                        self._live_store = None
                        self._live_settings = None
                        self._live_stop_evt = None
                        self._live_worker = None
                        self._live_capture_accepting = False
                        raise
                try:
                    self.manager.capture_state = "capturing"
                except Exception:
                    pass
            finally:
                if not attach_to_tracking:
                    self._live_starting = False
                self._poll_condition.notify_all()
        websocket_manager.publish_threadsafe("status", "session_created",
                                              session.summary())
        websocket_manager.publish_threadsafe("capture", "capture_started", {
            "session_id": session.id, "accelerometer": True, "rolling": True,
        })
        return session

    def stop_live_capture(self) -> bool:
        """Stop appending to the accelerometer rolling session."""
        with self._lock:
            active = self._live_session is not None
            attached = active and self._live_attached_to_tracking
            live_stop_evt = self._live_stop_evt
            live_worker = self._live_worker
            # An attached session must not stop the ordinary tracking ring;
            # it only stops accepting waveform/sample ownership for the
            # rolling session. A dedicated live worker gets its own event.
            if active:
                self._live_capture_accepting = False
            if live_stop_evt is not None and not attached:
                live_stop_evt.set()
            if active and getattr(self.manager, "capture_state", None) == "capturing":
                self.manager.capture_state = "cancelled"
        if (live_worker is not None and live_worker is not threading.current_thread()
                and live_worker.is_alive()):
            live_worker.join(timeout=1.0)
        with self._lock:
            persist_stop_evt = self._live_persist_stop_evt
            persist_evt = self._live_persist_evt
            persist_worker = self._live_persist_worker
            if persist_stop_evt is not None:
                persist_stop_evt.set()
            if persist_evt is not None:
                persist_evt.set()
        if (persist_worker is not None
                and persist_worker is not threading.current_thread()
                and persist_worker.is_alive()):
            persist_worker.join(timeout=1.0)
        with self._lock:
            self._live_session = None
            self._live_store = None
            self._live_settings = None
            self._live_stop_evt = None
            self._live_persist_stop_evt = None
            self._live_persist_evt = None
            self._live_persist_worker = None
            self._live_attached_to_tracking = False
            self._live_capture_accepting = False
            self._live_digital = np.zeros(0, dtype=np.uint16)
            self._live_persist_dirty = False
            self._live_dialogue_setup_ready = False
        # A persistent attach mirror avoids a register write on every live
        # fallback burst. Clear it once, after the producer has stopped, so
        # ordinary captures never inherit the accelerometer routing.
        device = getattr(self.manager, "device", None)
        cleanup = getattr(device, "accelerometer_capture_cleanup", None)
        if active and not attached and callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        if active:
            websocket_manager.publish_threadsafe(
                "capture", "capture_cancelled",
                {"message": "Accelerometer live stopped", "accelerometer": True})
        return active

    def _live_decoder_config(self) -> AccelerometerConfig:
        with self._lock:
            effective = self._status.effective_config
            if effective:
                try:
                    return AccelerometerConfig.model_validate(effective)
                except Exception:
                    pass
            return self._config

    def _live_decoder_padding_mask(self) -> int:
        """Return the active word padding for compatibility callers."""
        return _lis3dh_word_padding_mask(self._live_decoder_config())

    @property
    def live_capture_active(self) -> bool:
        with self._lock:
            return self._live_session is not None

    def _has_real_hardware(self) -> bool:
        device = getattr(self.manager, "device", None)
        return bool(
            getattr(self.manager, "device_kind", None) == "hardware"
            and device is not None
            and device.is_connected()
            and getattr(device, "_dev", None) is not None)

    def _raw_device(self) -> Any:
        if not self._has_real_hardware():
            raise HardwareError("No connected real hardware")
        raw = self.manager.device._dev
        if not hasattr(raw, "accel_read_i2c"):
            raise HardwareError("Connected hardware does not support LIS3DH reads")
        return raw

    def _run(self, generation: int, stop_evt: threading.Event) -> None:
        try:
            adapter = self.manager.device
            raw = self._raw_device()
            address = None
            for _ in range(LIS3DH_PROBE_ATTEMPTS):
                address = self._find_address(adapter, raw)
                if address is not None:
                    break
                if stop_evt.wait(LIS3DH_PROBE_RETRY_S):
                    return
            if address is None:
                raise HardwareError("LIS3DH accelerometer not detected")
            with self._lock:
                self._accelerometer_address = address
            self._configure(adapter, raw, address, self._config)
            # FIFO support is optional: older adapters can still provide the
            # atomic six-byte read path.  Keep setup failures non-fatal so a
            # partially upgraded host driver does not make tracking unusable.
            fifo_configure = getattr(adapter, "accelerometer_fifo_configure", None)
            if callable(fifo_configure):
                try:
                    fifo_configure(address, enabled=True, mode="stream",
                                   threshold=31, speed=FIFO_I2C_SPEED_HZ)
                except TypeError:
                    # Compatibility with adapters that predate the optional
                    # FIFO speed argument.
                    try:
                        fifo_configure(address, enabled=True, mode="stream",
                                       threshold=31)
                    except Exception:
                        pass
                except Exception:
                    pass
            with self._lock:
                self._last_fifo_error = None
            register_readback = self._read_config(adapter, raw, address)
            effective_config = self._decode_config(register_readback)
            decode_config = effective_config or self._config
            with self._lock:
                if generation != self._generation or stop_evt.is_set():
                    return
                self._status.available = True
                self._status.last_error = None
                self._status.effective_config = (
                    effective_config.model_dump() if effective_config else
                    self._config.model_dump())
                self._status.effective_odr_hz = float(
                    self._status.effective_config["output_data_rate_hz"])
                self._status.register_readback = register_readback

            # The regular board path must consume the real continuous
            # decoded stream too.  Keeping it on the old FIFO/atomic polling
            # loop made a configured 400 Hz sensor look like an ~11 Hz host,
            # even though the FPGA ring already contained many complete I2C
            # reads per USB round trip.  The producer owns the same worker
            # lifetime and adapter lock, so stop/teardown cannot leave a
            # background generator competing with a later capture.
            continuous_capture = getattr(
                raw, "continuous_ring_capture_with_repeating_i2c", None)
            # The capture ring is the single source for both pose and
            # waveform. At high ODRs the repeated read is a FIFO burst (the
            # live producer selects a 24-byte read), so the FPGA samples the
            # complete bus dialogue without a host round trip per XYZ point.
            use_continuous_stream = callable(continuous_capture)
            if use_continuous_stream:
                with self._lock:
                    self._continuous_stream_active = True
                try:
                    self._run_live_producer(
                        adapter, raw, address, stop_evt, None, decode_config)
                finally:
                    with self._lock:
                        self._continuous_stream_active = False
                        self._poll_condition.notify_all()
                return

            period = 1.0 / max(self.sample_rate_hz, 0.1)
            next_read = time.monotonic()
            invalid_reads = 0
            fifo_reader = getattr(adapter, "accelerometer_read_fifo", None)
            while not stop_evt.is_set():
                # The continuous live ring owns the Bit_Engine while it is
                # active.  Do not issue competing FIFO/status transactions;
                # the live producer decodes XYZ frames from the same ring.
                with self._lock:
                    live_active = (self._live_session is not None
                                   or self._live_starting)
                    if not live_active:
                        self._polling_io = True
                if live_active:
                    if stop_evt.wait(min(period, 0.05)):
                        return
                    next_read = time.monotonic()
                    continue
                try:
                    # Drain the FIFO in whole XYZ bursts at the fastest legal
                    # I²C speed. This preserves every available sensor point
                    # and is required for the 1.344 kHz normal/HR and 5.376
                    # kHz low-power ODRs.
                    if self.sample_rate_hz > 100 and not callable(fifo_reader):
                        samples = [self._read_xyz(
                            adapter, raw, address, decode_config)]
                    else:
                        samples = self._poll_xyz_samples(
                            adapter, raw, address, fifo_reader, decode_config)
                    invalid_reads = 0
                except HardwareError:
                    # A capture-visible generator burst briefly shares the
                    # same Bit_Engine link with the normal XYZ read.  A
                    # malformed response at that boundary is recoverable;
                    # killing the worker releases the reservation and makes
                    # the next Capture Live request report the misleading
                    # "stream is using the hardware" error.  Retry on the
                    # next poll and keep the service owner alive.
                    invalid_reads += 1
                    if invalid_reads >= 5:
                        with self._lock:
                            if generation == self._generation:
                                self._status.last_error = (
                                    "LIS3DH read temporarily unavailable; retrying")
                    if stop_evt.wait(min(period, 0.05)):
                        return
                    next_read = time.monotonic()
                    continue
                finally:
                    with self._lock:
                        self._polling_io = False
                        self._poll_condition.notify_all()
                for sample in samples:
                    if generation != self._generation or stop_evt.is_set():
                        return
                    # Keep the normal polling path and the rolling live path
                    # on one publication seam. Previously this path updated
                    # only REST status, leaving the presentation loop to poll
                    # at 100 ms even when the sensor was producing promptly.
                    # _record_sample also publishes the immediate websocket
                    # event consumed by the board renderer.
                    self._record_sample(sample)
                next_read += period
                delay = max(0.0, next_read - time.monotonic())
                if stop_evt.wait(delay):
                    return
                # Do not spin trying to catch up after a slow transaction.
                if next_read < time.monotonic() - period:
                    next_read = time.monotonic()
        except Exception as exc:
            with self._lock:
                if generation == self._generation and not stop_evt.is_set():
                    self._status.running = False
                    self._status.available = False
                    self._status.last_error = str(exc)
        finally:
            with self._lock:
                if generation == self._generation:
                    # Cancellation before setup completes is not a failed
                    # probe.  Let the next status call derive readiness from
                    # the still-connected hardware instead of leaving the
                    # UI permanently unavailable after a quick stop.
                    if stop_evt.is_set() and self._status.last_error is None:
                        self._availability_known = False
                    self._continuous_stream_active = False
                    self._status.running = False
                    self._stop_evt = None
                    self._worker = None
            release = getattr(self.manager, "release_accelerometer", None)
            if callable(release):
                release()

    @staticmethod
    def _find_address(adapter: Any, raw: Any) -> Optional[int]:
        probe = getattr(adapter, "accelerometer_probe", None)
        if callable(probe):
            return probe()
        for address in LIS3DH_ADDRESSES:
            with _adapter_lock(adapter):
                try:
                    if raw.accel_read_i2c(LIS3DH_WHO_AM_I, dev_addr=address) == LIS3DH_ID:
                        return address
                except Exception:
                    continue
        return None

    @staticmethod
    def _read_config(adapter: Any, raw: Any, address: int) -> Optional[dict[str, int]]:
        """Read back the two LIS3DH control registers when supported.

        Some older host adapters expose only XYZ reads.  In that case the
        effective configuration is still reported as the requested settings,
        while ``register_readback`` remains null instead of inventing values.
        """
        read_config = getattr(adapter, "accelerometer_read_config", None)
        if callable(read_config):
            try:
                result = read_config(address)
                if isinstance(result, dict):
                    ctrl1 = result.get("ctrl1")
                    ctrl4 = result.get("ctrl4")
                    if isinstance(ctrl1, int) and isinstance(ctrl4, int):
                        return {"ctrl1": ctrl1 & 0xFF, "ctrl4": ctrl4 & 0xFF}
            except Exception:
                return None
        # Do not issue ad-hoc register reads through an older raw driver:
        # those drivers may not serialize this operation and may return a
        # burst payload for every register.  The bundled adapter exposes the
        # explicit, locked readback seam above.
        return None

    @staticmethod
    def _decode_config(registers: Optional[dict[str, int]]) -> Optional[AccelerometerConfig]:
        if not registers:
            return None
        ctrl1 = int(registers.get("ctrl1", 0))
        ctrl4 = int(registers.get("ctrl4", 0))
        odr_code = (ctrl1 >> 4) & 0x0F
        if odr_code == 8:
            # CTRL_REG1 code 8 is only defined at 1.60 kHz with LPen set.
            odr = 1600 if ctrl1 & 0x08 else None
        elif odr_code == 9:
            # Code 9 is 1.344 kHz in normal/high-resolution mode and
            # 5.376 kHz in low-power mode.
            odr = 5376 if ctrl1 & 0x08 else 1344
        else:
            odr = {1: 1, 2: 10, 3: 25, 4: 50, 5: 100, 6: 200,
                   7: 400}.get(odr_code)
        scale = {0: 2, 1: 4, 2: 8, 3: 16}.get((ctrl4 >> 4) & 0x03)
        if odr is None or scale is None:
            return None
        if ctrl1 & 0x08:
            mode = "low_power"
        elif ctrl4 & 0x08:
            mode = "high_resolution"
        else:
            mode = "normal"
        return AccelerometerConfig(
            output_data_rate_hz=odr, full_scale_g=scale,
            operating_mode=mode, bdu=bool(ctrl4 & 0x80),
            endian="big" if ctrl4 & 0x40 else "little",
            self_test={0: "normal", 2: "positive", 4: "negative"}.get(
                ctrl4 & 0x06, "normal"),
            x_enabled=bool(ctrl1 & 0x01), y_enabled=bool(ctrl1 & 0x02),
            z_enabled=bool(ctrl1 & 0x04))

    @staticmethod
    def _configure(adapter: Any, raw: Any, address: int,
                   config: AccelerometerConfig | None = None) -> None:
        config = config or AccelerometerConfig()
        configure = getattr(adapter, "accelerometer_configure", None)
        if callable(configure):
            try:
                configure(address, config=config)
            except TypeError:
                # Compatibility with older adapters that only accept the
                # address; the bundled adapter below accepts configuration.
                configure(address)
            return
        writer = getattr(raw, "accel_write_i2c", None)
        if not callable(writer):
            raise HardwareError("Connected hardware cannot configure LIS3DH")
        ctrl1, ctrl4 = _config_registers(config)
        with _adapter_lock(adapter):
            for register, value in ((LIS3DH_CTRL_REG1, ctrl1),
                                    (LIS3DH_CTRL_REG4, ctrl4)):
                if writer(register, value, dev_addr=address) is False:
                    raise HardwareError(
                        f"Failed to configure LIS3DH register 0x{register:02x}")

    @staticmethod
    def _read_xyz(adapter: Any, raw: Any, address: int,
                  config: AccelerometerConfig | None = None
                  ) -> tuple[float, float, float]:
        read_xyz = getattr(adapter, "accelerometer_read_xyz", None)
        if callable(read_xyz):
            payload = read_xyz(address)
        else:
            with _adapter_lock(adapter):
                try:
                    payload = raw.accel_read_i2c(
                        LIS3DH_OUT_X_L, dev_addr=address, read_len=6)
                except TypeError:
                    # Keep compatibility with older out-of-tree host drivers
                    # while retaining the atomic bundled-driver path.
                    payload = bytes(
                        raw.accel_read_i2c(LIS3DH_OUT_X_L + offset,
                                           dev_addr=address)
                        for offset in range(6))
        return AccelerometerService._decode_xyz(payload, config)

    def _poll_xyz_samples(self, adapter: Any, raw: Any, address: int,
                          fifo_reader: Any,
                          config: AccelerometerConfig | None = None
                          ) -> list[tuple[float, float, float]]:
        """Read one polling batch after claiming the shared bus.

        ``_run`` marks ``_polling_io`` before entering this helper.  Live
        startup waits for that claim to clear, so this operation cannot race
        the stream's generator setup and abort it underneath the producer.
        """
        samples: list[tuple[float, float, float]] = []
        if callable(fifo_reader):
            try:
                try:
                    fifo_payload = fifo_reader(
                        address, max_samples=25, speed=FIFO_I2C_SPEED_HZ)
                except TypeError:
                    fifo_payload = fifo_reader(address, max_samples=25)
                if (isinstance(fifo_payload, (bytes, bytearray))
                        and fifo_payload):
                    if len(fifo_payload) % 6:
                        raise HardwareError(
                            "LIS3DH returned an invalid FIFO payload")
                    samples = [self._decode_xyz(
                        fifo_payload[offset:offset + 6], config)
                        for offset in range(0, len(fifo_payload), 6)]
            except Exception as exc:
                # FIFO is an optimization, not a prerequisite for tracking.
                # Fall back to the proven single-read path on unsupported,
                # empty, or malformed FIFO responses.
                with self._lock:
                    self._last_fifo_error = (
                        f"{type(exc).__name__}: {exc}" or type(exc).__name__)
                samples = []
        if not samples:
            samples = [self._read_xyz(adapter, raw, address, config)]
        return samples

    @staticmethod
    def _decode_xyz(payload: Any, config: AccelerometerConfig | None = None
                    ) -> tuple[float, float, float]:
        """Decode one six-byte LIS3DH XYZ frame using its active layout."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) != 6:
            raise HardwareError("LIS3DH returned an invalid XYZ sample")
        config = config or AccelerometerConfig()
        shift = _LIS3DH_WORD_SHIFT[config.operating_mode]
        sensitivity = _LIS3DH_SENSITIVITY_MG[config.operating_mode][
            config.full_scale_g]
        values = []
        for offset in (0, 2, 4):
            raw_value = int.from_bytes(
                payload[offset:offset + 2], config.endian, signed=True)
            values.append((raw_value >> shift) * sensitivity / 1000.0)
        return values[0], values[1], values[2]

    def _run_live_producer(self, adapter: Any, raw: Any, address: int,
                           live_stop_evt: threading.Event,
                           owner_session: Session | None,
                           decoder_config: AccelerometerConfig | None = None,
                           *, force_ring: bool = False,
                           transport_fallback_error: str | None = None
                           ) -> None:
        """Feed tracking or a live session from the FPGA ring.

        ``owner_session`` is ``None`` for ordinary board tracking.  A rolling
        session can attach to that producer while it is running; the ring
        remains the single bus owner and only waveform persistence changes.
        """
        with self._lock:
            effective = self._status.effective_config
            if effective:
                try:
                    decoder_config = AccelerometerConfig.model_validate(effective)
                except Exception:
                    pass
            decoder_config = decoder_config or self._config
        sample_lsb_mask = _lis3dh_word_padding_mask(decoder_config)
        stream = None
        stream_started_at = None
        stream_transitions = 0
        stream_frames = 0
        stream_waveform_samples = 0
        stream_watchdog = False
        transport_error = transport_fallback_error
        direct_capable = (not force_ring and bool(
            getattr(raw, "supports_direct_live_stream", False)))
        with self._lock:
            self._live_transport = {
                "mode": "direct" if direct_capable else "ring",
                "state": "starting",
                "fallback": bool(transport_fallback_error),
                "error": transport_fallback_error,
                "started_at": self._clock(),
            }
        try:
            capture = getattr(raw, "continuous_ring_capture_with_repeating_i2c", None)
            if callable(capture):
                live_schedule = _live_i2c_schedule(
                    decoder_config.output_data_rate_hz)
                live_read_len = int(live_schedule["read_len"])
                fifo_burst = live_read_len > LIVE_TRACE_READ_LEN
                if direct_capable:
                    live_window_samples = LIVE_DIRECT_STREAM_WINDOW_SAMPLES
                else:
                    live_window_samples = (
                        LIVE_HIGH_ODR_STREAM_WINDOW_SAMPLES
                        if fifo_burst else LIVE_STREAM_WINDOW_SAMPLES)
                stream = capture(
                    rate_hz=LIVE_TRACE_RATE_HZ,
                    # Use one decoder-safe tail window per delivery. The
                    # source ring is deliberately oversampled; the browser's
                    # latest-value mailbox, rather than host prefetch, is the
                    # place where presentation work is coalesced.
                    chunk_nsamp=live_window_samples,
                    buffer_nsamp=32768,
                    stop_evt=live_stop_evt,
                    dev_addr=address,
                    reg_addr=LIS3DH_OUT_X_L | 0x80,
                    read_len=live_read_len,
                    i2c_speed=int(live_schedule["i2c_speed"]),
                    repeat_transactions=LIVE_TRACE_REPEAT_TRANSACTIONS,
                    compact_i2c=bool(live_schedule["compact_i2c"]),
                    repeat_rate_hz=live_schedule["repeat_rate_hz"],
                    force_ring=force_ring,
                    # Use the validated block-read ring transport for the
                    # live path. The optional held-CS raw serializer is not
                    # supported reliably by the flashed image: it can return
                    # an idle window even though CMD_GEN_CAPTURE accepted the
                    # repeating source, which trips the watchdog and drops
                    # live mode to the slow host-burst reader. Follow the
                    # newest block-read tail explicitly: starting at the
                    # ring's oldest sample replays roughly 2 seconds of
                    # stale history before the board catches up.
                    use_rle=False,
                    prefer_raw_stream=False,
                    follow_tail=True,
                    fetch_multiplier=LIVE_STREAM_FETCH_MULTIPLIER,
                    include_cursor=True)
                # The hardware ring may yield very small chunks (especially
                # on older bitstreams).  Persisting every chunk serializes
                # the producer on the session store and throttles decoding.
                # Batch waveform writes while retaining per-frame XYZ status
                # updates; waveform persistence is not on the pose seam.
                pending: list[np.ndarray] = []
                pending_samples = 0
                pending_session: Session | None = None
                last_flush = time.monotonic()
                waveform_sequence = 0
                waveform_pending: list[np.ndarray] = []
                waveform_pending_samples = 0
                persist_quantum = (LIVE_DIRECT_WAVEFORM_BATCH_SAMPLES
                                   if direct_capable else LIVE_TRACE_SAMPLES)

                def flush_waveform_pending() -> None:
                    nonlocal waveform_pending, waveform_pending_samples
                    nonlocal waveform_sequence
                    if not waveform_pending:
                        return
                    waveform = (waveform_pending[0]
                                if len(waveform_pending) == 1 else
                                np.concatenate(waveform_pending))
                    waveform_pending = []
                    waveform_pending_samples = 0
                    waveform_sequence += 1
                    raw_samples = np.asarray(
                        waveform, dtype="<u2").tobytes()
                    header = json.dumps({
                            "sequence": waveform_sequence,
                            "sample_rate": LIVE_TRACE_RATE_HZ,
                            "sample_count": int(waveform.size),
                            "source_index": None,
                            "ring_timing": None,
                            "config": decoder_config.model_dump(),
                            "address": address,
                            "read_length": live_read_len,
                        }, separators=(",", ":")).encode("utf-8")
                    # Keep the raw sample payload 16-bit aligned after the
                    # four-byte header-length prefix.
                    if len(header) & 1:
                        header += b" "
                    packet = struct.pack("<I", len(header)) + header + raw_samples
                    has_binary_consumer = getattr(
                        websocket_manager, "has_subscribers", None)
                    binary_published = False
                    if (callable(has_binary_consumer)
                            and has_binary_consumer(
                                "accelerometer_waveform_binary")):
                        publish_binary = getattr(
                            websocket_manager, "publish_binary_threadsafe", None)
                        if callable(publish_binary):
                            publish_binary(
                                "accelerometer_waveform_binary", packet)
                            binary_published = True
                    # Direct mode has one canonical waveform transport. The
                    # worker subscribes to both endpoints for compatibility,
                    # so sending the same quantum on text and binary would
                    # decode the physical waveform twice.
                    if (not binary_published and (
                            not callable(has_binary_consumer)
                            or has_binary_consumer("accelerometer_waveform"))):
                        websocket_manager.publish_threadsafe(
                            "accelerometer_waveform",
                            "accelerometer_waveform", {
                                "sequence": waveform_sequence,
                                "sample_rate": LIVE_TRACE_RATE_HZ,
                                "sample_count": int(waveform.size),
                                "source_index": None,
                                "samples_b64": base64.b64encode(
                                    raw_samples).decode("ascii"),
                                "ring_timing": None,
                                "config": decoder_config.model_dump(),
                                "address": address,
                                "read_length": live_read_len,
                            })

                stream_decoder = _LiveI2CFrameDecoder(
                    address, sample_lsb_mask, decoder_config,
                    clock=self._clock, read_len=live_read_len)
                sample_gate = _LiveI2CFrameGate()
                # Waveform writes can rebuild a 4096-sample rolling file and
                # publish websocket work. Start the coalescing persister
                # before consuming the held-CS stream so those operations do
                # not block the physical sample reader.
                self._start_live_persister()
                # The stream uses the raw driver directly, while the normal
                # adapter seams serialize through ``adapter._lock``. Hold
                # that same lock for the complete producer lifetime so a
                # late poll (or another adapter operation) cannot interleave
                # a CMD_ABORT_CAPTURE between two stream windows.
                stream_io_lock = _adapter_lock(adapter)
                stream_io_lock.__enter__()

                def current_waveform_session() -> Session | None:
                    with self._lock:
                        if owner_session is not None:
                            if (self._live_session is owner_session
                                    and self._live_capture_accepting):
                                return owner_session
                            return None
                        session = self._live_session
                        if session is not None and self._live_capture_accepting:
                            return session
                        return None

                def flush_pending() -> None:
                    nonlocal pending, pending_samples, pending_session, last_flush
                    # Direct mode's ordered binary waveform is the live
                    # source of truth.  Do not concatenate it into the
                    # rolling session while the same worker is draining the
                    # FPGA FIFO: at a 2 MHz tap that copy/lock path can stall
                    # the physical reader and turn into a FIFO overrun.
                    if direct_capable:
                        pending = []
                        pending_samples = 0
                        pending_session = None
                        last_flush = time.monotonic()
                        return
                    if pending and pending_session is not None:
                        self._append_live_digital(
                            np.concatenate(pending),
                            expected_session=pending_session)
                        pending = []
                        pending_samples = 0
                        pending_session = None
                    last_flush = time.monotonic()

                def publish_gate_items(items: list[tuple[Any, tuple[float, float, float]]]
                                       ) -> None:
                    # The decoder has already validated the complete bus
                    # frame. Keep every such frame on the FIFO waveform/
                    # history topic, while the latest pose topic gets one
                    # additional causal physical-plausibility check. That
                    # check never waits for a future point.
                    for metadata, sample in sample_gate.accept_indexed_immediately(items):
                        source_index = None
                        received_at = None
                        chunk_end = None
                        decoded_at = None
                        ring_timing = None
                        if isinstance(metadata, dict):
                            source_index = metadata.get("source_index")
                            received_at = metadata.get("received_at")
                            chunk_end = metadata.get("chunk_end")
                            decoded_at = metadata.get("decoded_at")
                            ring_timing = metadata.get("ring_timing")
                        elif isinstance(metadata, tuple) and len(metadata) == 3:
                            source_index, received_at, chunk_end = metadata
                        timestamp = None
                        if (source_index is not None
                                and isinstance(ring_timing, dict)
                                and ring_timing.get("producer_index") is not None
                                and ring_timing.get("read_started_at") is not None):
                            source_backlog = _live_ring_age(
                                int(ring_timing["producer_index"]),
                                int(source_index))
                            # The ring producer pointer is the age reference.
                            # Use its pre-read timestamp, not the time the
                            # backlog reached the host.
                            timestamp = (
                                float(ring_timing["read_started_at"])
                                - source_backlog / LIVE_TRACE_RATE_HZ)
                        elif (source_index is not None
                              and received_at is not None
                              and chunk_end is not None):
                            source_span = _live_ring_distance(
                                int(chunk_end), int(source_index))
                            timestamp = float(received_at) - (
                                min(source_span, LIVE_TRACE_RING_HALF)
                                / LIVE_TRACE_RATE_HZ)
                        gate_accepted_at = self._clock()
                        self._record_sample(
                            sample, owner_session=owner_session,
                            timestamp=timestamp, source_index=source_index,
                            decoded_at=decoded_at,
                            gate_accepted_at=gate_accepted_at,
                            ring_timing=ring_timing,
                            pose_sample=sample,
                            pose_accepted=True)

                def reset_stream_state() -> None:
                    # A tail jump is a transport boundary, not permission to
                    # throw away a complete validated frame; flush it before
                    # resetting decoder and both live gates.
                    flush_waveform_pending()
                    publish_gate_items(sample_gate.flush_indexed())
                    stream_decoder.reset()
                    sample_gate.reset()

                try:
                    stream_overrun = 0
                    for item in stream:
                        if live_stop_evt.is_set():
                            break
                        if stream_started_at is None:
                            stream_started_at = time.monotonic()
                            with self._lock:
                                if self._live_transport is not None:
                                    self._live_transport["state"] = "streaming"
                        if isinstance(item, tuple):
                            # Raw tail-follow windows are deliberately not
                            # contiguous: the ring advances several windows
                            # while one SPI transaction is in flight.  Reset
                            # the incremental decoder at that boundary so a
                            # partial I2C transaction cannot be completed
                            # with bits from a later window.
                            item_overrun = int(item[3]) if len(item) > 3 else 0
                            if item_overrun > stream_overrun:
                                reset_stream_state()
                                stream_overrun = item_overrun
                            chunk = item[0]
                            chunk_start = (int(item[4])
                                           if len(item) > 4 and item[4] is not None
                                           else None)
                            ring_timing = (item[5]
                                            if len(item) > 5
                                            and isinstance(item[5], dict)
                                            else None)
                        else:
                            chunk = item
                            chunk_start = None
                            ring_timing = None
                        if direct_capable and isinstance(chunk, (bytes, bytearray)):
                            # The direct parser has already produced dense
                            # little-endian 16-bit words containing only
                            # CH13..CH15.  Keep this seam zero-copy: making a
                            # second NumPy copy for every 16K window adds
                            # avoidable GIL/allocator pressure at the 2 MHz
                            # tap and can starve the bounded wire reader.
                            digital = np.frombuffer(chunk, dtype="<u2")
                        elif isinstance(chunk, (bytes, bytearray)):
                            digital = payload_to_digital(chunk)
                        else:
                            digital = np.asarray(chunk, dtype=np.uint16).reshape(-1)
                        # Held-CS tail-follow windows are not contiguous when
                        # USB/SPI cannot consume the source ring in
                        # real time. Never carry an I2C transaction across
                        # samples that were not observed: joining unrelated
                        # windows can create believable but incorrect XYZ
                        # jumps even though the bus waveform looks healthy.
                        consume_gap = getattr(raw, "consume_stream_gap", None)
                        if callable(consume_gap):
                            try:
                                if consume_gap():
                                    reset_stream_state()
                            except Exception:
                                # Advisory metadata must never take down the
                                # live capture on older driver implementations.
                                pass
                        # At the 1 MHz live tap the normal four-symbol I2C
                        # encoder has only a couple of capture points in
                        # each high plateau.  Applying the non-causal
                        # three-point filter to a long FIFO burst can move
                        # the first/last edge of each burst and destroy the
                        # ACK/STOP sequence even though the raw waveform is
                        # valid.  High-ODR bursts are already protocol-
                        # validated by the incremental decoder; preserve
                        # those samples verbatim.  The short 400 Hz path
                        # retains the historical glitch filter.
                        if not direct_capable:
                            digital = clean_live_trace(
                                digital,
                                # Ring fallback retains the historical filter
                                # for the legacy low-rate path. Direct words
                                # are already reduced to the three capture
                                # lines above and must remain phase-exact.
                                majority=(LIVE_TRACE_RATE_HZ
                                          >= 4 * LIVE_TRACE_I2C_SPEED_HZ
                                          and float(decoder_config.output_data_rate_hz)
                                          <= 400.0))
                        if digital.size:
                            stream_waveform_samples += int(digital.size)
                            with self._lock:
                                if self._live_transport is not None:
                                    self._live_transport[
                                        "waveform_samples"] = stream_waveform_samples
                            # The frontend decoder consumes this exact packed
                            # waveform stream. Keep it separate from the
                            # backend's decoded pose topic and from waveform
                            # persistence; neither is allowed to become the
                            # live decoder's source of truth.
                            received_at = self._clock()
                            if direct_capable:
                                waveform_pending.append(digital)
                                waveform_pending_samples += int(digital.size)
                                if (waveform_pending_samples
                                        >= LIVE_DIRECT_WAVEFORM_BATCH_SAMPLES):
                                    flush_waveform_pending()
                            else:
                                waveform_sequence += 1
                                websocket_manager.publish_threadsafe(
                                    "accelerometer_waveform",
                                    "accelerometer_waveform", {
                                        "sequence": waveform_sequence,
                                        "sample_rate": LIVE_TRACE_RATE_HZ,
                                        "sample_count": int(digital.size),
                                        "source_index": chunk_start,
                                        "samples_b64": base64.b64encode(
                                            np.asarray(digital, dtype="<u2").tobytes()
                                        ).decode("ascii"),
                                        "ring_timing": ring_timing,
                                        "config": decoder_config.model_dump(),
                                    })
                            chunk_end = (_live_ring_distance(
                                         int(chunk_start) + int(digital.size), 0)
                                         if chunk_start is not None else None)
                            if not direct_capable:
                                stream_transitions += int(np.count_nonzero(
                                    digital[1:] != digital[:-1]))
                            if not direct_capable:
                                waveform_session = current_waveform_session()
                                if waveform_session is not pending_session:
                                    # Do not let bytes observed before a
                                    # rolling session attached (or after it
                                    # stopped) leak into a later session.
                                    pending = []
                                    pending_samples = 0
                                    pending_session = waveform_session
                                if waveform_session is not None:
                                    pending.append(digital)
                                    pending_samples += int(digital.size)
                            # In direct mode the browser worker owns the raw
                            # waveform decode. Running a second Python I2C
                            # decoder here at the 1 MHz tap only contends for
                            # the GIL with the USB reader and can make the
                            # FPGA FIFO overflow when a browser is connected.
                            # Keep the backend decoder for ring mode and for
                            # headless direct/status consumers; never duplicate
                            # it while the ordered waveform topic is active.
                            # Direct mode is always owned by the ordered
                            # waveform consumer. Subscriber presence is a
                            # connection-time race and must not decide
                            # whether the backend duplicates the 1 MHz
                            # decoder; ring mode is the only backend-decoded
                            # path.
                            frontend_decodes = direct_capable
                            if frontend_decodes:
                                indexed = []
                            elif chunk_start is not None:
                                decoded_indexed = stream_decoder.feed_indexed_timed(
                                    digital, chunk_start)
                                indexed = [
                                    ({
                                        "source_index": position,
                                        "received_at": received_at,
                                        "chunk_end": chunk_end,
                                        "decoded_at": decoded_at,
                                        "ring_timing": ring_timing,
                                    }, sample)
                                    for position, sample, decoded_at
                                    in decoded_indexed]
                            else:
                                # Direct FIFO samples do not carry a ring
                                # cursor.  Timestamping every I2C byte inside
                                # ``feed_timed`` adds a wall-clock call to the
                                # hot path for no useful ordering information;
                                # the batch was observed at ``received_at``
                                # and the waveform itself remains lossless.
                                decoded_plain = stream_decoder.feed(digital)
                                indexed = [
                                    ({
                                        "source_index": None,
                                        "received_at": received_at,
                                        "chunk_end": None,
                                        "decoded_at": received_at,
                                        "ring_timing": None,
                                    }, sample)
                                    for sample in decoded_plain]
                            stream_frames += len(indexed)
                            if indexed:
                                publish_gate_items(indexed)
                            if (not direct_capable and (
                                    pending_samples >= persist_quantum
                                    or time.monotonic() - last_flush >= 0.25)):
                                flush_pending()
                            frontend_decodes = direct_capable
                            if (stream_frames == 0 and not frontend_decodes
                                    and stream_started_at is not None
                                    and time.monotonic() - stream_started_at
                                    >= LIVE_STREAM_WATCHDOG_S):
                                stream_watchdog = True
                                break
                    flush_waveform_pending()
                    publish_gate_items(sample_gate.flush_indexed())
                    flush_pending()
                finally:
                    stream_io_lock.__exit__(None, None, None)
                if live_stop_evt.is_set():
                    return
        except Exception as exc:
            transport_error = str(exc)
            with self._lock:
                if self._live_transport is not None:
                    self._live_transport.update({
                        "state": "fallback",
                        "fallback": True,
                        "error": str(exc),
                    })
                diagnostic_session = None
                if owner_session is not None:
                    if self._live_session is owner_session:
                        diagnostic_session = owner_session
                elif self._live_session is not None:
                    diagnostic_session = self._live_session
                if diagnostic_session is not None:
                    diagnostic_session.diagnostics.append({
                        "level": "warning", "message": str(exc),
                        "ts": self._clock()})
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if stream_watchdog:
            transport_error = "stream produced no validated bus frames"
            with self._lock:
                if owner_session is None:
                    self._continuous_stream_watchdog = True
                if self._live_transport is not None:
                    self._live_transport.update({
                        "state": "fallback",
                        "fallback": True,
                        "error": (
                            "stream produced no validated bus frames"),
                    })
                diagnostic_session = None
                if owner_session is not None:
                    if self._live_session is owner_session:
                        diagnostic_session = owner_session
                elif self._live_session is not None:
                    diagnostic_session = self._live_session
                if diagnostic_session is not None:
                    diagnostic_session.diagnostics.append({
                        "level": "warning",
                        "message": (
                            "Accelerometer stream produced no validated bus frames; "
                            "using bounded host-burst fallback"),
                        "ts": self._clock(),
                    })
        if (direct_capable and not force_ring
                and not live_stop_evt.is_set()):
            # Direct capture is the live decoder's source of truth. Never
            # retry it through the SDRAM ring: that would make a transport
            # failure look like a healthy pose stream while decoding stale
            # history. Leave the service in an explicit fault state so the
            # hardware/transport defect can be fixed and measured.
            with self._lock:
                if self._live_transport is not None:
                    self._live_transport.update({
                        "mode": "direct",
                        "state": "fault",
                        "fallback": False,
                        "error": transport_error,
                    })
                if self._status.running:
                    self._status.last_error = (
                        "Direct live waveform transport failed: "
                        f"{transport_error}")
            return
        # The persister is normally already running for a session-owned ring
        # path. This call is idempotent and also covers a stream implementation
        # that exits before the setup block above can start it. Ordinary
        # tracking has no store until a rolling session attaches.
        self._start_live_persister()
        # Older images, and images without the repeating-I2C capability, use
        # a bounded one-shot reader on this producer thread. Decode every
        # complete frame in the burst (rather than publishing only one sample
        # per trace). The capture trace is deliberately the only bus
        # transaction in this degraded mode: draining the LIS3DH FIFO would
        # add a status read plus one RX-register transaction per returned byte
        # and can hold the shared link for more than a second.
        period = min(1.0 / max(self.sample_rate_hz, 0.1),
                     LIVE_FALLBACK_MAX_INTERVAL_S)
        while not live_stop_evt.is_set():
            if owner_session is None:
                # Ordinary tracking still needs a useful path on an older
                # image whose ring/generator command is present but idle.
                # Use atomic XYZ reads here; _capture_live_trace deliberately
                # requires a rolling session and cannot be the tracking
                # fallback.
                with self._lock:
                    attached_session = (
                        self._live_session
                        if self._live_capture_accepting else None)
                if attached_session is not None:
                    self._capture_live_trace(
                        adapter, raw, address, live_stop_evt,
                        nsamples=LIVE_FALLBACK_TRACE_SAMPLES,
                        repeat_count=LIVE_FALLBACK_REPEAT_COUNT,
                        expected_session=attached_session,
                        decoder_config=decoder_config,
                        sample_callback=lambda sample: self._record_sample(
                            sample, owner_session=attached_session))
                else:
                    try:
                        self._record_sample(
                            self._read_xyz(adapter, raw, address, decoder_config))
                    except HardwareError:
                        pass
                live_stop_evt.wait(period)
                continue

            frames = self._capture_live_trace(
                adapter, raw, address, live_stop_evt,
                nsamples=LIVE_FALLBACK_TRACE_SAMPLES,
                repeat_count=LIVE_FALLBACK_REPEAT_COUNT,
                expected_session=owner_session,
                decoder_config=decoder_config,
                sample_callback=lambda sample: self._record_sample(
                    sample, owner_session=owner_session))
            # Keep status tracking alive when a short trace ends between I2C
            # frames (or when the legacy capture has no attach mirror).
            if not frames:
                try:
                    self._record_sample(
                        self._read_xyz(adapter, raw, address, decoder_config),
                        owner_session=owner_session)
                except HardwareError:
                    pass
            live_stop_evt.wait(period)

    @staticmethod
    def _decode_live_i2c_frames(digital: np.ndarray,
                                address: int,
                                config: AccelerometerConfig | None = None
                                ) -> list[tuple[float, float, float]]:
        """Decode complete six-byte LIS3DH reads from an attach-ring chunk.

        The generator repeats ``addrW, register, addrR, X..Z``.  SDA/SCL are
        mirrored on CH13/CH14.  Sampling SDA in the middle of each SCL-high
        plateau matches the normal I2C decoder and avoids edge settling.
        """
        return _LiveI2CFrameDecoder(address, config=config).feed(digital)

    def _record_sample(self, sample: tuple[float, float, float],
                       owner_session: Session | None = None,
                       timestamp: float | None = None,
                       source_index: int | None = None,
                       decoded_at: float | None = None,
                       gate_accepted_at: float | None = None,
                       ring_timing: dict[str, Any] | None = None,
                       pose_sample: tuple[float, float, float] | None = None,
                       pose_accepted: bool = True) -> None:
        """Publish a decoded XYZ sample and update host-throughput counters.

        A live producer can outlive ``stop_live_capture`` while a hardware
        readback call unwinds. Tie its samples to the session that created the
        worker so a late frame cannot leak into a later capture or the normal
        tracking stream.
        """
        published_at = self._clock()
        timestamp = (float(timestamp) if timestamp is not None
                     and np.isfinite(timestamp) else published_at)
        measured_at = time.monotonic()
        pushed: dict[str, Any] | None = None
        pose_pushed: dict[str, Any] | None = None
        with self._lock:
            if not self._status.running:
                return
            if (owner_session is not None
                    and (self._live_session is not owner_session
                         or not self._live_capture_accepting)):
                return
            previous_sample = self._status.sample
            previous_timestamp = (previous_sample.get("timestamp")
                                  if previous_sample else None)
            if (isinstance(previous_timestamp, (int, float))
                    and timestamp <= previous_timestamp):
                # A ring cursor can wrap or an older adapter can omit exact
                # transport timing. Never let the frontend watermark move
                # backwards when that approximation occurs.
                timestamp = float(previous_timestamp) + 1e-6
            self._status.sample = {
                "x_g": sample[0], "y_g": sample[1], "z_g": sample[2],
                "timestamp": timestamp,
            }
            self._status.last_sample_at = timestamp
            self._status.last_error = None
            self._status.samples_read += 1
            self._sample_sequence += 1

            source_backlog = 0
            source_age = 0
            if (source_index is not None and isinstance(ring_timing, dict)
                    and ring_timing.get("producer_index") is not None):
                producer_index = int(ring_timing["producer_index"])
                source_age = _live_ring_age(
                    producer_index, int(source_index))
                consumer_index = ring_timing.get("consumer_index")
                if consumer_index is not None:
                    source_backlog = _live_ring_age(
                        producer_index, int(consumer_index))
                else:
                    # Compatibility with older raw drivers that did not
                    # report the end of the transferred window.
                    source_backlog = source_age
            read_started_at = (
                ring_timing.get("read_started_at")
                if isinstance(ring_timing, dict) else None)
            ring_rate = (
                float(ring_timing.get("ring_sample_rate_hz", LIVE_TRACE_RATE_HZ))
                if isinstance(ring_timing, dict) else LIVE_TRACE_RATE_HZ)
            capture_to_publish_ms = 0.0
            if read_started_at is not None:
                capture_to_publish_ms = (
                    max(0.0, float(published_at) - float(read_started_at))
                    + source_age / max(ring_rate, 1.0)) * 1000.0
            elif decoded_at is not None and np.isfinite(decoded_at):
                capture_to_publish_ms = max(
                    0.0, float(published_at) - float(decoded_at)) * 1000.0
            decoded_to_publish_ms = 0.0
            if decoded_at is not None and np.isfinite(decoded_at):
                decoded_to_publish_ms = max(
                    0.0, float(published_at) - float(decoded_at)) * 1000.0
            trace: dict[str, Any] = {
                "sequence": self._sample_sequence,
                "source_sample_index": source_index,
                "decoded_at": decoded_at,
                "gate_accepted_at": gate_accepted_at,
                "published_at": float(published_at),
                "source_backlog_samples": source_backlog,
                "source_age_samples": source_age,
                "capture_to_publish_latency_ms": capture_to_publish_ms,
                "decoded_to_publish_latency_ms": decoded_to_publish_ms,
            }
            if isinstance(ring_timing, dict):
                trace["ring"] = dict(ring_timing)
            self._status.last_sample_sequence = self._sample_sequence
            self._status.source_sample_index = source_index
            self._status.source_backlog_samples = source_backlog
            self._status.source_age_samples = source_age
            self._status.capture_to_publish_latency_ms = capture_to_publish_ms
            self._status.decoded_to_publish_latency_ms = decoded_to_publish_ms
            self._status.live_timing = dict(trace)
            if self._rate_window_started is None:
                self._rate_window_started = measured_at
                self._rate_window_samples = 1
            else:
                self._rate_window_samples += 1
                elapsed = measured_at - self._rate_window_started
                if elapsed >= 0.25:
                    rate = self._rate_window_samples / elapsed
                    self._status.host_read_rate_hz = rate
                    self._status.host_data_rate_bytes_per_s = rate * 6.0
                    self._status.measured_rate_hz = rate
                    self._status.data_rate_bytes_per_s = rate * 6.0
                    self._rate_window_started = measured_at
                    self._rate_window_samples = 0
            # Keep sample delivery separate from waveform_ready notifications.
            # The latter can be relatively expensive for a rolling session and
            # is intentionally coalesced; this tiny message drives the live
            # orientation model at the decoder's actual cadence.
            pushed = {
                "sample": dict(self._status.sample),
                "last_sample_at": timestamp,
                "samples_read": self._status.samples_read,
                "host_read_rate_hz": self._status.host_read_rate_hz,
                "host_data_rate_bytes_per_s": self._status.host_data_rate_bytes_per_s,
                "measured_rate_hz": self._status.measured_rate_hz,
                "data_rate_bytes_per_s": self._status.data_rate_bytes_per_s,
                "last_sample_sequence": self._status.last_sample_sequence,
                "source_sample_index": self._status.source_sample_index,
                "source_backlog_samples": self._status.source_backlog_samples,
                "source_age_samples": self._status.source_age_samples,
                "capture_to_publish_latency_ms": (
                    self._status.capture_to_publish_latency_ms),
                "decoded_to_publish_latency_ms": (
                    self._status.decoded_to_publish_latency_ms),
                "trace": trace,
            }
            if pose_accepted:
                pose_pushed = dict(pushed)
                if pose_sample is not None:
                    pose_pushed["sample"] = {
                        "x_g": pose_sample[0], "y_g": pose_sample[1],
                        "z_g": pose_sample[2], "timestamp": timestamp,
                    }
        if pushed is not None:
            publish_latest = getattr(
                websocket_manager, "publish_latest_threadsafe", None)
            # Direct FIFO samples have no ring cursor and are a presentation
            # stream, not archival history. When the asyncio loop is active,
            # replace stale board targets instead of making the live decoder
            # compete with a second ordered JSON queue at ~1 kHz. The raw
            # waveform topic remains the lossless ordered source.
            if (source_index is None and callable(publish_latest)
                    and getattr(websocket_manager, "_loop", None) is not None):
                publish_latest(
                    "accelerometer", "accelerometer_sample", pushed)
            else:
                websocket_manager.publish_threadsafe(
                    "accelerometer", "accelerometer_sample", pushed)
            if callable(publish_latest) and pose_pushed is not None:
                publish_latest(
                    "accelerometer_pose", "accelerometer_sample", pose_pushed)

    def _append_live_digital(self, digital: np.ndarray,
                             expected_session: Session | None = None) -> None:
        with self._lock:
            session = self._live_session
            store = self._live_store
            settings = self._live_settings
            if session is None or store is None or settings is None:
                return
            if (expected_session is not None
                    and (session is not expected_session
                         or not self._live_capture_accepting)):
                return
            if self._live_persist_worker is not None:
                merged = np.concatenate((self._live_digital, digital))[
                    -int(settings.num_samples):]
                self._live_digital = merged
                self._live_persist_dirty = True
                persist_evt = self._live_persist_evt
                if persist_evt is not None:
                    persist_evt.set()
                return
            current = store.load_waveform(session.id)
            previous = (current.digital if current is not None
                        and current.digital is not None else
                        np.zeros(0, dtype=np.uint16))
            merged = np.concatenate((previous, digital))[-int(settings.num_samples):]
            store.save_waveform(session.id, WaveformData(
                sample_rate=LIVE_TRACE_RATE_HZ, digital=merged))
            session.num_samples = int(len(merged))
            session.sample_rate = LIVE_TRACE_RATE_HZ
            store.save(session)
            session_id = session.id
        websocket_manager.publish_threadsafe(
            f"session:{session_id}", "waveform_ready", {
                "session_id": session_id, "num_samples": int(len(merged)),
                "chunk_samples": int(len(digital)),
                "sample_rate": LIVE_TRACE_RATE_HZ, "rolling": True,
                "accelerometer": True,
            })
        schedule = getattr(self.manager, "schedule_live_decode", None)
        if callable(schedule):
            schedule(session_id)

    def _start_live_persister(self) -> None:
        """Start coalesced waveform persistence for the one-shot fallback."""
        with self._lock:
            if (self._live_session is None
                    or self._live_persist_worker is not None):
                return
            stop_evt = threading.Event()
            wake_evt = threading.Event()
            self._live_persist_stop_evt = stop_evt
            self._live_persist_evt = wake_evt
            self._live_persist_worker = threading.Thread(
                target=self._run_live_persister,
                args=(stop_evt, wake_evt), daemon=True,
                name="lis3dh-live-persister")
            self._live_persist_worker.start()

    def _run_live_persister(self, stop_evt: threading.Event,
                            wake_evt: threading.Event) -> None:
        """Flush the newest rolling waveform without blocking bus sampling."""
        deadline = 0.0
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            wake_evt.wait(timeout)
            wake_evt.clear()
            if time.monotonic() < deadline and not stop_evt.is_set():
                continue
            with self._lock:
                session = self._live_session
                store = self._live_store
                if (session is None or store is None
                        or not self._live_persist_dirty):
                    if stop_evt.is_set():
                        return
                    continue
                settings = self._live_settings
                digital = self._live_digital.copy()
                self._live_persist_dirty = False
                session_copy = session.model_copy(deep=True)
                session_copy.num_samples = int(digital.size)
                session_copy.sample_rate = LIVE_TRACE_RATE_HZ
                session_id = session.id
            try:
                store.save_waveform(session_id, WaveformData(
                    sample_rate=LIVE_TRACE_RATE_HZ, digital=digital))
                store.save(session_copy)
                websocket_manager.publish_threadsafe(
                    f"session:{session_id}", "waveform_ready", {
                        "session_id": session_id,
                        "num_samples": int(digital.size),
                        "chunk_samples": int(digital.size),
                        "sample_rate": LIVE_TRACE_RATE_HZ, "rolling": True,
                        "accelerometer": True,
                    })
                schedule = getattr(self.manager, "schedule_live_decode", None)
                if callable(schedule):
                    schedule(session_id)
            except Exception as exc:
                with self._lock:
                    if self._live_session is session:
                        session.diagnostics.append({
                            "level": "warning", "message": str(exc),
                            "ts": self._clock()})
            deadline = time.monotonic() + LIVE_WAVEFORM_PERSIST_INTERVAL_S
            if stop_evt.is_set():
                # The snapshot above is the final one after the producer has
                # stopped; do not wait for another interval during teardown.
                return

    def _capture_live_trace(self, adapter: Any, raw: Any, address: int,
                            stop_evt: threading.Event, *,
                            nsamples: int | None = None,
                            repeat_count: int = 3,
                            expected_session: Session | None = None,
                            decoder_config: AccelerometerConfig | None = None,
                            sample_callback: Callable[
                                [tuple[float, float, float]], None] | None = None
                            ) -> list[tuple[float, float, float]]:
        with self._lock:
            session = self._live_session
            store = self._live_store
            settings = self._live_settings
        if (session is None or store is None or settings is None
                or stop_evt.is_set()
                or (expected_session is not None
                    and session is not expected_session)):
            return []
        try:
            capture_samples = min(
                LIVE_TRACE_SAMPLES,
                max(1, int(nsamples if nsamples is not None
                            else int(settings.num_samples))))
            with self._lock:
                reuse_setup = self._live_dialogue_setup_ready
            payload = self._read_dialogue(
                adapter, raw, address,
                capture_samples, repeat_count=max(1, int(repeat_count)),
                reuse_setup=reuse_setup, keep_attached=True)
            with self._lock:
                if self._live_session is session:
                    self._live_dialogue_setup_ready = True
            digital = clean_live_trace(payload_to_digital(payload))
            if digital.size == 0:
                return []
            frames = self._decode_live_i2c_frames(
                digital, address, decoder_config)
            with self._lock:
                # stop_live_capture can race with a completed bus transaction.
                if self._live_session is not session or self._live_store is not store:
                    return []
            # Publish decoded XYZ as soon as the capture bytes are available.
            # Waveform persistence and decoder scheduling can involve a much
            # larger rolling buffer; keeping them after this callback prevents
            # storage latency from inflating the physical-sample age.
            if sample_callback is not None:
                for frame in frames:
                    sample_callback(frame)
            self._append_live_digital(digital, expected_session=expected_session)
            return frames
        except Exception as exc:
            # A failed optional trace must not terminate the regular XYZ
            # service; status polling remains useful even if capture is not.
            with self._lock:
                if self._live_session is session:
                    session.diagnostics.append({"level": "warning",
                                                "message": str(exc),
                                                "ts": self._clock()})
            return []

    def _read_dialogue(self, adapter: Any, raw: Any, address: int,
                       nsamples: int, repeat_count: int = 1,
                       reuse_setup: bool = False,
                       keep_attached: bool = False) -> bytes:
        capture_trace = getattr(adapter, "accelerometer_capture_trace", None)
        if callable(capture_trace):
            try:
                return capture_trace(address, rate_hz=LIVE_TRACE_RATE_HZ,
                                     nsamples=nsamples,
                                     repeat_count=repeat_count,
                                     reuse_setup=reuse_setup,
                                     timeout=LIVE_TRACE_TIMEOUT_S,
                                     keep_attached=keep_attached)
            except TypeError:
                # Keep out-of-tree adapters compatible while the bundled raw
                # driver gains the bounded host-burst argument.
                return capture_trace(address, rate_hz=LIVE_TRACE_RATE_HZ,
                                     nsamples=nsamples)
        capture = getattr(raw, "accel_capture_dialogue", None)
        if not callable(capture):
            raise HardwareError("Connected hardware cannot capture LIS3DH dialogue")
        # Import lazily: ftd2xx is not required by normal/mock backend tests.
        try:
            from driver import bit_bang
        except ImportError:
            from ..hardware.protocol import HOST_DIR
            import sys
            if str(HOST_DIR) not in sys.path:
                sys.path.insert(0, str(HOST_DIR))
            from driver import bit_bang
        dev_w = (int(address) << 1) & 0xFE
        syms = bit_bang.i2c_read_symbols(
            bytes([dev_w, LIS3DH_OUT_X_L | 0x80]), 6, dev_w | 1)
        sys_clk = float(getattr(raw, "sys_clk", 100_000_000) or 100_000_000)
        bit_div = max(1, int(round(
            sys_clk / (4 * LIVE_TRACE_I2C_SPEED_HZ) - 1.25)))
        with _adapter_lock(adapter):
            data = capture(syms, bit_div, spi_test=False,
                           rate_hz=LIVE_TRACE_RATE_HZ, nsamples=nsamples,
                           repeat_count=repeat_count,
                           reuse_setup=reuse_setup,
                           timeout=LIVE_TRACE_TIMEOUT_S,
                           keep_attached=keep_attached)
        if not isinstance(data, (bytes, bytearray)):
            raise HardwareError("LIS3DH dialogue returned invalid data")
        return bytes(data)


accelerometer = AccelerometerService()


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _adapter_lock(adapter: Any):
    """Use the adapter's shared lock for compatibility fake adapters."""
    return getattr(adapter, "_lock", _NullLock())
