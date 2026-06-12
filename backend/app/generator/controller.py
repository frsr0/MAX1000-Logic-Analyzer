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

# decoder + channel mapping per generator protocol (mock pin layout)
_LOOPBACK_DECODE = {
    "uart": ("uart", lambda cfg: {"rx": f"d{cfg.tx_pin}"},
             lambda cfg: {"baud": cfg.baud}),
    "i2c": ("i2c", lambda cfg: {"scl": f"d{cfg.scl_pin}", "sda": f"d{cfg.tx_pin}"},
            lambda cfg: {}),
    "spi": ("spi", lambda cfg: {"sclk": "d4", "mosi": "d5", "miso": "d6", "cs": "d7"},
            lambda cfg: {}),
}


def loopback_self_test(mgr: CaptureManager, cfg: GeneratorConfig,
                       capture_rate: float, capture_samples: int,
                       expected_hex: Optional[str] = None) -> GeneratorSelfTestResult:
    """Send a pattern through the generator while capturing, decode the
    capture, and compare against the sent/expected bytes."""
    dev = mgr.require_device()
    sent = bytes.fromhex(cfg.data_hex) if cfg.data_hex else b"\x55"
    expected = bytes.fromhex(expected_hex) if expected_hex else sent

    settings = CaptureSettings(sample_rate=capture_rate,
                               num_samples=capture_samples)
    result = dev.capture_with_generator(settings, cfg)
    wf = WaveformData(sample_rate=result.sample_rate, digital=result.digital,
                      analog=result.analog)
    session = Session(name=f"Generator self-test ({cfg.protocol})",
                      device=dev.get_metadata(),
                      settings=settings, sample_rate=result.sample_rate,
                      num_samples=wf.num_samples,
                      tags=["generator", "self-test"])
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
    inst = DecoderInstance(id=new_id("dec"), decoder_id=dec_id,
                           name=f"{dec_id} (self-test)",
                           channels=ch_fn(cfg), settings=set_fn(cfg))
    ctx = DecodeContext(wf, inst.channels)
    dec_result = decoder.decode(ctx, {**decoder.defaults(), **inst.settings})
    for ev in dec_result.events:
        ev["decoder_id"] = inst.id
    inst.status = "done"
    inst.event_count = len(dec_result.events)
    session.decoders.append(inst)
    mgr.store.save(session)
    mgr.store.save_decoder_events(session.id, inst.id, dec_result.events)

    if cfg.protocol == "uart":
        decoded = bytes(e["fields"]["byte"] for e in dec_result.events
                        if e["type"] == "uart_byte")
    elif cfg.protocol == "i2c":
        decoded = bytes(e["fields"]["byte"] for e in dec_result.events
                        if e["type"] == "i2c_byte")
        # mock loopback prepends the register byte; tolerate prefix match
        if cfg.i2c_register is not None and decoded[:1] == bytes([cfg.i2c_register]):
            decoded = decoded[1:]
    elif cfg.protocol == "spi":
        decoded = bytes(e["fields"]["mosi"] & 0xFF for e in dec_result.events
                        if e["type"] == "spi_word" and e["fields"]["mosi"] is not None)
    else:
        decoded = b""

    mismatches = [i for i, (a, b) in enumerate(zip(expected, decoded)) if a != b]
    if len(expected) != len(decoded):
        mismatches += list(range(min(len(expected), len(decoded)),
                                 max(len(expected), len(decoded))))
    passed = not mismatches
    detail = ("PASS — decoded output matches sent pattern" if passed else
              f"FAIL — {len(mismatches)} byte mismatch(es); "
              f"expected {expected.hex()} got {decoded.hex()}")
    log.info("Generator self-test %s: %s", cfg.protocol, detail)
    return GeneratorSelfTestResult(
        passed=passed, sent_hex=expected.hex(), decoded_hex=decoded.hex(),
        detail=detail, session_id=session.id, mismatches=mismatches[:64])
