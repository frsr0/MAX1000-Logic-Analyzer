#!/usr/bin/env python3
"""Hardware smoke test for the web host app.

Run this ON THE MACHINE THE FPGA IS PLUGGED INTO (FTDI D2XX driver +
'pip install ftd2xx' required):

    cd backend
    python hw_smoke_test.py            # real hardware
    python hw_smoke_test.py --mock     # validate the script against the mock

It exercises the exact code path the web app uses (CaptureManager ->
ExistingHostAdapter -> host/driver/OLSDeviceSPI):

  1. device discovery
  2. connect + metadata (sample clock auto-detect)
  3. capabilities
  4. device self-test (debug CH0 PWM -> capture -> edge count)
  5. plain digital capture (1 MHz, 4096 samples) + sanity checks
  6. UART generator loopback (CMD_GEN_CAPTURE) -> UART decode -> byte compare

Exit code 0 = all checks passed. Sessions created by the test are saved and
visible in the web UI afterwards.
"""
import argparse
import sys
import time

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager           # noqa: E402
from app.capture.sample_format import WaveformData, find_edges   # noqa: E402
from app.capture.session import CaptureSettings                  # noqa: E402
from app.capture.session_store import SessionStore               # noqa: E402
from app.diagnostics.sanity_checks import run_sanity_checks      # noqa: E402
from app.generator.controller import loopback_self_test          # noqa: E402
from app.hardware.base import HardwareError                      # noqa: E402
from app.hardware.device_models import GeneratorConfig           # noqa: E402

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


class Check:
    def __init__(self):
        self.results = []

    def run(self, name, fn):
        t0 = time.time()
        try:
            detail = fn() or ""
            self.results.append((name, True, str(detail), time.time() - t0))
            print(f"  {GREEN}PASS{RESET}  {name}  {detail}")
        except Exception as e:
            self.results.append((name, False, str(e), time.time() - t0))
            print(f"  {RED}FAIL{RESET}  {name}  {e}")

    @property
    def passed(self):
        return all(ok for _, ok, _, _ in self.results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true",
                    help="run against the mock device (script self-check)")
    ap.add_argument("--rate", type=float, default=1_000_000)
    ap.add_argument("--samples", type=int, default=4096)
    args = ap.parse_args()
    device_id = "mock" if args.mock else "hardware"

    store = SessionStore()
    mgr = CaptureManager(store)
    c = Check()
    print(f"MAX1000 hardware smoke test — target: {device_id}\n")

    # 1. discovery
    def discovery():
        devs = {d["id"]: d for d in mgr.list_devices()}
        d = devs[device_id]
        if not d["available"]:
            raise HardwareError(d["detail"] or "device not available")
        return d["name"]
    c.run("device discovery", discovery)

    # 2. connect + metadata
    def connect():
        meta = mgr.connect(device_id)
        clk = meta["sample_clk_hz"]
        if not args.mock and clk not in (200e6, 120e6):
            return (f"sample_clk={clk / 1e6:.0f} MHz "
                    f"{YELLOW}(expected 200 speed / 120 normal){RESET}")
        return f"{meta['device_name']}, sample_clk={clk / 1e6:.0f} MHz"
    c.run("connect + metadata", connect)
    if not mgr.device or not mgr.device.is_connected():
        print(f"\n{RED}Cannot continue without a connection.{RESET}")
        return 1

    # 3. capabilities
    c.run("capabilities", lambda: (
        f"{mgr.device.get_capabilities().digital_channels} digital ch, "
        f"max {mgr.device.get_capabilities().max_sample_rate / 1e6:.0f} MHz, "
        f"gen: {','.join(mgr.device.get_capabilities().generator_protocols)}"))

    # 4. self-test (debug CH0 PWM loopback on hardware)
    def self_test():
        r = mgr.device.self_test()
        fails = [ck for ck in r["checks"] if not ck["passed"]]
        if fails:
            raise HardwareError("; ".join(
                f"{ck['name']}: {ck['detail']}" for ck in fails))
        return "; ".join(f"{ck['name']} ok" for ck in r["checks"])
    c.run("device self-test", self_test)

    # 5. plain capture + sanity checks
    session_holder = {}

    def capture():
        settings = CaptureSettings(sample_rate=args.rate,
                                   num_samples=args.samples,
                                   mock_scenario="demo_mixed")
        result = mgr.device.capture(settings)
        if result.digital is None or len(result.digital) == 0:
            raise HardwareError("capture returned no samples")
        wf = WaveformData(sample_rate=result.sample_rate,
                          digital=result.digital)
        session = mgr._result_to_session(settings, result,
                                         "HW smoke test capture", 1)
        session_holder["s"] = session
        edges_total = sum(
            len(find_edges(wf.digital_channel(ch), "any")) for ch in range(16))
        note = "" if not result.warnings else f" warnings={result.warnings}"
        return (f"{len(result.digital)} samples, {edges_total} edges total "
                f"-> session {session.id}{note}")
    c.run(f"digital capture {args.samples}@{args.rate / 1e6:g}MHz", capture)

    def sanity():
        s = session_holder.get("s")
        if s is None:
            raise HardwareError("no capture session")
        wf = store.load_waveform(s.id)
        findings = run_sanity_checks(s, wf)
        errors = [f for f in findings if f["level"] == "error"]
        warns = [f for f in findings if f["level"] == "warning"]
        if errors:
            raise HardwareError("; ".join(f["message"] for f in errors))
        return (f"{len(findings)} findings, {len(warns)} warnings"
                + (f" ({warns[0]['message']})" if warns else ""))
    c.run("capture sanity checks", sanity)

    # 6. UART generator loopback -> decode -> compare
    def loopback():
        cfg = GeneratorConfig(protocol="uart", data_hex="48656c6c6f21",
                              baud=115200, tx_pin=0 if args.mock else 3)
        r = loopback_self_test(mgr, cfg, capture_rate=2_000_000,
                               capture_samples=20_000)
        if not r.passed:
            raise HardwareError(r.detail)
        return f"{r.detail} -> session {r.session_id}"
    c.run("UART generator loopback + decode", loopback)

    mgr.disconnect()
    ok = c.passed
    n_pass = sum(1 for _, p, _, _ in c.results if p)
    print(f"\n{'=' * 60}")
    colour = GREEN if ok else RED
    print(f"{colour}{'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'}{RESET} "
          f"({n_pass}/{len(c.results)})")
    if ok:
        print("Sessions saved — start the server (python run.py) and inspect "
              "them in the web UI.")
    else:
        print("For deeper hardware diagnosis run the full validation suite:\n"
              "  cd ../host && python -m app.hw_validation")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
