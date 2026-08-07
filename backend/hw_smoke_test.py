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
  4. device self-test (metadata + status/control-plane checks)
  5. plain digital capture (10 MHz, 40960 samples, ~4.1 ms) + sanity checks
  6. UART / RS-485 / SPI / SWD generator loopback (CMD_GEN_CAPTURE) -> decode -> compare

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
DEFAULT_CAPTURE_RATE = 10_000_000
DEFAULT_CAPTURE_DURATION_S = 4.096e-3


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
    ap.add_argument("--rate", type=float, default=DEFAULT_CAPTURE_RATE,
                    help="digital capture rate in samples/sec (default: 10 MHz)")
    ap.add_argument("--samples", type=int, default=None,
                    help="digital capture sample count (default: preserve ~4.1 ms window)")
    args = ap.parse_args()
    if args.samples is None:
        args.samples = max(1, int(round(args.rate * DEFAULT_CAPTURE_DURATION_S)))
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
    def capabilities():
        caps = mgr.device.get_capabilities()
        routes = {r.protocol: r for r in caps.generator_routes}
        spi_features = {"cs", "miso"} if args.mock else {"cs_pin", "miso_pin"}
        for protocol, features in (("rs485", {"de_pin"}),
                                    ("spi", spi_features)):
            route = routes.get(protocol)
            if route is None or not features.issubset(set(route.features)):
                raise HardwareError(
                    f"{protocol} route missing auxiliary features {sorted(features)}")
        return (f"{caps.digital_channels} digital ch, "
                f"max {caps.max_sample_rate / 1e6:.0f} MHz, "
                f"gen: {','.join(caps.generator_protocols)}, "
                "RS-485 DE + SPI CS/MISO routes advertised")
    c.run("capabilities", capabilities)

    # 4. self-test (lightweight hardware/control-plane checks)
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

    # 6. Generator loopback routes that are physically supported by the
    # current adapter. I2C is intentionally excluded: it needs a connected
    # external slave, unlike the internal UART/RS-485/SPI/SWD routes.
    route_configs = [
        ("uart", 115200, 0 if args.mock else 3, 1),
        ("rs485", 115200, 0 if args.mock else 3, 1),
        ("spi", 1_000_000, 5 if args.mock else 3, 4 if args.mock else 1),
        ("swd", 1_000_000, 1 if args.mock else 3, 0 if args.mock else 1),
    ]
    for protocol, baud, tx_pin, scl_pin in route_configs:
        def loopback(protocol=protocol, baud=baud, tx_pin=tx_pin, scl_pin=scl_pin):
            cfg = GeneratorConfig(protocol=protocol, data_hex="4142",
                                  baud=baud, tx_pin=tx_pin, scl_pin=scl_pin,
                                  extra=(
                                      {"requests": [{"ap": False, "read": True,
                                                     "addr": 0, "data": 0}]}
                                      if protocol == "swd" else
                                      {"de_pin": 6} if protocol == "rs485" else
                                      {"cs_pin": 7, "cs_capture_channel": 14,
                                       "miso_pin": 23,
                                       "miso_capture_channel": 15}
                                      if protocol == "spi" else {}))
            r = loopback_self_test(mgr, cfg, capture_rate=2_000_000,
                                   capture_samples=8_000)
            if not r.passed:
                raise HardwareError(r.detail)
            return f"{r.detail} -> session {r.session_id}"
        c.run(f"{protocol.upper()} generator loopback + decode", loopback)

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
