"""Generator workflow: configure -> loopback capture -> auto-decode -> compare.

Works on mock (synthetic loopback) and real hardware (CMD_GEN_CAPTURE atomic
generator+capture path in the existing driver)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..capture.capture_manager import CaptureManager
from ..capture.sample_format import WaveformData
from ..capture.session import (CaptureSettings, DecoderInstance, Session,
                               default_digital_channels, new_id)
from ..decoders import registry as decoder_registry
from ..decoders.base import DecodeContext
from ..hardware.device_models import GeneratorConfig
from ..websocket.manager import manager
from .model import GeneratorSelfTestResult

log = logging.getLogger("msa.generator")

MAX_GENERATOR_PAYLOAD_BYTES = 256
UART_BITS_PER_BYTE = 10
UART_CAPTURE_GUARD_SAMPLES = 2_000
UART_CAPTURE_MARGIN = 1.20

# decoder + channel mapping per generator protocol (mock pin layout)
_LOOPBACK_DECODE = {
    "uart": ("uart", lambda cfg: {"rx": f"d{cfg.tx_pin}"},
             lambda cfg: {"baud": cfg.baud}),
    "rs485": ("uart", lambda cfg: {"rx": f"d{cfg.tx_pin}"},
              lambda cfg: {"baud": cfg.baud}),
    "i2c": ("i2c", lambda cfg: {"scl": f"d{cfg.scl_pin}", "sda": f"d{cfg.tx_pin}"},
            lambda cfg: {}),
    "spi": ("spi", lambda cfg: {"sclk": "d4", "mosi": "d5", "miso": "d6", "cs": "d7"},
            lambda cfg: {}),
    "swd": ("swd", lambda cfg: {"swclk": f"d{cfg.scl_pin}", "swdio": f"d{cfg.tx_pin}"},
            lambda cfg: {"glitch_filter": int(cfg.extra.get("glitch_filter", 0))}),
}


def generator_payload_bytes(cfg: GeneratorConfig) -> bytes:
    return bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"


def validate_generator_payload(cfg: GeneratorConfig) -> bytes:
    data = generator_payload_bytes(cfg)
    if len(data) > MAX_GENERATOR_PAYLOAD_BYTES:
        raise ValueError(
            f"{cfg.protocol.upper()} generator payload is {len(data)} bytes; "
            f"current FPGA generator FIFO holds {MAX_GENERATOR_PAYLOAD_BYTES} bytes")
    if cfg.protocol == "rs485" and int(cfg.tx_pin) == int(cfg.scl_pin):
        raise ValueError("RS-485 generator A and B pins must be different")
    if cfg.protocol == "spi":
        # SPI packs 16 Bit_Engine symbols/byte vs UART's 10, so the shared
        # FIFO holds far fewer SPI bytes than the generic cap above allows.
        from driver import bit_bang as _bb  # imported via HOST_DIR shim
        limit = _bb.max_spi_bytes()
        if len(data) > limit:
            raise ValueError(
                f"SPI generator payload is {len(data)} bytes; the Bit_Engine "
                f"symbol FIFO holds at most {limit} SPI bytes")
    return data


def required_uart_capture_samples(cfg: GeneratorConfig, capture_rate: float) -> int:
    data = validate_generator_payload(cfg)
    baud = max(1, int(cfg.baud))
    payload_samples = len(data) * UART_BITS_PER_BYTE * float(capture_rate) / baud
    return int(payload_samples * UART_CAPTURE_MARGIN) + UART_CAPTURE_GUARD_SAMPLES


def normalized_loopback_samples(cfg: GeneratorConfig, capture_rate: float,
                                requested_samples: int) -> int:
    if cfg.protocol not in ("uart", "rs485"):
        return requested_samples
    return max(int(requested_samples), required_uart_capture_samples(cfg, capture_rate))


def loopback_self_test(mgr: CaptureManager, cfg: GeneratorConfig,
                       capture_rate: float, capture_samples: int,
                       expected_hex: Optional[str] = None) -> GeneratorSelfTestResult:
    """Send a pattern through the generator while capturing, decode the
    capture, and compare against the sent/expected bytes."""
    dev = mgr.require_device()
    validate_route = getattr(dev, "validate_generator_config", None)
    if callable(validate_route):
        validate_route(cfg)
    sent = validate_generator_payload(cfg)
    expected = bytes.fromhex(expected_hex) if expected_hex else sent
    capture_samples = normalized_loopback_samples(cfg, capture_rate, capture_samples)

    settings = CaptureSettings(sample_rate=capture_rate,
                               num_samples=capture_samples)
    # One retry covers an occasional generator/capture arming race; the
    # capture data itself is reliable since the firmware CDC fixes.
    outcome = _loopback_attempt(mgr, dev, cfg, settings, expected)
    if not outcome.passed:
        outcome = _loopback_attempt(mgr, dev, cfg, settings, expected)
    return outcome


def _loopback_attempt(mgr: CaptureManager, dev, cfg: GeneratorConfig,
                      settings: CaptureSettings,
                      expected: bytes) -> GeneratorSelfTestResult:
    sent = generator_payload_bytes(cfg)
    result = dev.capture_with_generator(settings, cfg)
    wf = WaveformData(sample_rate=result.sample_rate, digital=result.digital,
                      analog=result.analog)
    session = Session(name=f"Generator self-test ({cfg.protocol})",
                      device=dev.get_metadata(),
                      settings=settings, sample_rate=result.sample_rate,
                      num_samples=wf.num_samples,
                      tags=["generator", "self-test"],
                      generator={"config": cfg.model_dump(),
                                 "expected_hex": expected.hex(),
                                 "sent_hex": sent.hex()})
    session.channels = default_digital_channels(16)
    mgr.store.save(session)
    mgr.store.save_waveform(session.id, wf)
    mgr.last_session_id = session.id
    manager.publish_threadsafe("status", "session_created", session.summary())

    spec = _LOOPBACK_DECODE.get(cfg.protocol)
    if spec is None:
        return GeneratorSelfTestResult(
            passed=True, sent_hex=sent.hex(), decoded_hex="",
            session_id=session.id,
            detail=f"Pattern sent and captured; no decoder defined for "
                   f"'{cfg.protocol}' — inspect the waveform manually")

    dec_id, ch_fn, set_fn = spec
    decoder = decoder_registry.get(dec_id)
    decoder_settings = {**decoder.defaults(), **set_fn(cfg)}
    if cfg.protocol == "spi" and mgr.device_kind != "mock":
        # Real hardware only loops MOSI+SCLK into the capture (no CS/MISO —
        # see capture_with_generator's SPI branch), onto whatever pins the
        # user configured, unlike the mock's fixed CH4-7 SPI scenario.
        channels = {"sclk": f"d{cfg.scl_pin}", "mosi": f"d{cfg.tx_pin}"}
    else:
        channels = ch_fn(cfg)
    inst = DecoderInstance(id=new_id("dec"), decoder_id=dec_id,
                           name=f"{dec_id} (self-test)",
                           channels=channels, settings=decoder_settings)
    ctx = DecodeContext(wf, inst.channels)
    dec_result = decoder.decode(ctx, inst.settings)
    for ev in dec_result.events:
        ev["decoder_id"] = inst.id
    inst.status = "done"
    inst.event_count = len(dec_result.events)
    session.decoders.append(inst)
    mgr.store.save(session)
    mgr.store.save_decoder_events(session.id, inst.id, dec_result.events)

    if cfg.protocol in ("uart", "rs485"):
        nacked = []
        decoded = bytes(e["fields"]["byte"] for e in dec_result.events
                        if e["type"] == "uart_byte")
    elif cfg.protocol == "i2c":
        i2c_events = [
            e for e in dec_result.events
            if e["type"] in ("i2c_address", "i2c_byte")
        ]
        nacked = [
            e for e in i2c_events
            if not e["fields"].get("ack", False)
            and (e["type"] == "i2c_address" or e["fields"].get("rw") != "read")
        ]
        decoded = bytes(e["fields"]["byte"] for e in dec_result.events
                        if e["type"] == "i2c_byte")
        # mock loopback prepends the register byte; tolerate prefix match
        if cfg.i2c_register is not None and decoded[:1] == bytes([cfg.i2c_register]):
            decoded = decoded[1:]
    elif cfg.protocol == "spi":
        nacked = []
        decoded = bytes(e["fields"]["mosi"] & 0xFF for e in dec_result.events
                        if e["type"] == "spi_word" and e["fields"]["mosi"] is not None)
    elif cfg.protocol == "swd":
        nacked = []
        decoded = b""
    if cfg.protocol in ("uart", "rs485"):
        passed, mismatches, detail = _compare_uart_loopback(expected, decoded)
    elif cfg.protocol == "swd":
        transfers = sum(1 for event in dec_result.events
                        if event["type"] == "swd_xfer")
        passed = transfers > 0
        mismatches = []
        detail = (f"PASS - captured and decoded {transfers} SWD transaction(s)"
                  if passed else "FAIL - no SWD transactions decoded")
    else:
        mismatches = [i for i, (a, b) in enumerate(zip(expected, decoded)) if a != b]
        if len(expected) != len(decoded):
            mismatches += list(range(min(len(expected), len(decoded)),
                                     max(len(expected), len(decoded))))
        passed = not mismatches
        detail = ("PASS - decoded output matches sent pattern" if passed else
                  f"FAIL - {len(mismatches)} byte mismatch(es); "
                  f"expected {expected.hex()} got {decoded.hex()}")
    if cfg.protocol == "i2c" and nacked:
        passed = False
        nack_labels = ", ".join(e.get("label", e["type"]) for e in nacked[:4])
        detail = ("FAIL - I2C slave did not ACK the transaction "
                  f"({len(nacked)} NACK event(s): {nack_labels}); "
                  f"expected {expected.hex()} got {decoded.hex()}")
    log.info("Generator self-test %s: %s", cfg.protocol, detail)
    return GeneratorSelfTestResult(
        passed=passed, sent_hex=expected.hex(), decoded_hex=decoded.hex(),
        detail=detail, session_id=session.id, mismatches=mismatches[:64])


def _compare_uart_loopback(expected: bytes, decoded: bytes) -> tuple[bool, list[int], str]:
    if decoded == expected:
        return True, [], "PASS - decoded output matches sent pattern"

    offset = decoded.find(expected)
    if offset >= 0:
        prefix = offset
        suffix = len(decoded) - offset - len(expected)
        detail = "PASS - decoded UART stream contains sent pattern"
        if prefix or suffix:
            detail += f" (ignored {prefix} leading/{suffix} trailing decoded byte(s))"
        return True, [], detail

    mismatches = [i for i, (a, b) in enumerate(zip(expected, decoded)) if a != b]
    if len(expected) != len(decoded):
        mismatches += list(range(min(len(expected), len(decoded)),
                                 max(len(expected), len(decoded))))
    return (
        False,
        mismatches,
        f"FAIL - {len(mismatches)} byte mismatch(es); "
        f"expected {expected.hex()} got {decoded.hex()}",
    )
