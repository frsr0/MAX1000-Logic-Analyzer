# Verification and Change Traceability

This page is the audit index for changes that affect the FPGA image, host
driver, backend contract, or frontend behavior. A feature is not considered
hardware-validated merely because its source or simulation test exists.

## Evidence chain

```mermaid
flowchart LR
    CHANGE[Source change] --> UNIT[Unit or host test]
    CHANGE --> SIM[HDL simulation]
    CHANGE --> BUILD[Quartus compile and post-fit STA]
    BUILD --> IMAGE[Programmed SOF]
    IMAGE --> HW[Connected-board regression]
    UNIT --> CLAIM[Documented claim]
    SIM --> CLAIM
    BUILD --> CLAIM
    HW --> CLAIM
```

The evidence levels used below are:

| Level | Meaning |
|---|---|
| SW | Host, backend, or frontend tests only; no FPGA claim |
| SIM | HDL testbench passes in simulation |
| BUILD | RTL compiles and required post-fit timing paths close |
| HW | Exact programmed image passes the relevant connected-board test |

## Current exact-image hardware validation

On 2026-07-22, the current seed-23 SOF was rebuilt, timing-analyzed, and
programmed with checksum `0x004FDDF3`. The full connected-board regression
recorded **369/369 passed, 0 failed, 0 skipped** after the digital loopback
jumper was installed and discovered as pool pin 22 to capture channel 13.

## Changes since the previous complete hardware validation

| Commit | Change | Documentation | Evidence status |
|---|---|---|---|
| `89b84898` | Bit Engine hardware repeat mode; host `repeat=True` flag | [Signal Generator](hdl/signal-generator.md), [Generator Routing](generator-routing.md) | **HW** on SOF `0x004FDDF3`; full regression passed |
| `6f506855` | Keep FAST capture input pipeline in LE registers with `AUTO_SHIFT_REGISTER_RECOGNITION OFF` | [Build Flow](hdl/build-flow.md), [FAST Capture Stream](hdl/fast-capture-stream.md) | **BUILD + HW** on SOF `0x004FDDF3` |
| `ef7d4171` | Add narrow packed FAST regression to `tb_fast_analyzer` | [HDL Testbenches](hdl/testbenches.md), [FAST Capture Stream](hdl/fast-capture-stream.md) | **SIM + HW** on SOF `0x004FDDF3` |

The latest complete board evidence is the 2026-07-22 run documented in
[Hardware Validation](hardware-validation.md). Any later RTL change requires
the build, image checksum, and relevant hardware test result to be recorded
before changing a claim from BUILD or SIM to HW.

## Verification layers

```mermaid
flowchart TD
    RTL[HDL RTL] --> TB[Focused HDL testbenches]
    TB --> HDL[HDL regression]
    RTL --> Q[Quartus fit and STA]
    Q --> SOF[SOF checksum]
    SOF --> BOARD[Board smoke and feature tests]
    DRIVER[Host driver] --> PY[Host/backend tests]
    BOARD --> REPORT[Validation report]
    PY --> REPORT
    FRONTEND[Frontend] --> E2E[Playwright mock/live tests]
    E2E --> REPORT
    HDL --> REPORT
```

## Audit rules

- Record the commit, build profile, fitter seed, SOF checksum, and test date
  beside every HW claim.
- Keep simulation-only and host-only evidence explicitly labelled; neither
  proves board routing, clock timing, or electrical response.
- Re-run post-fit STA after RTL, SDC, QSF, or fitter changes. The FAST build is
  seed-sensitive at its current 98% logic utilisation.
- Update this page and the [Feature Matrix](feature-matrix.md) when a new
  hardware-facing register, route, capture mode, or validation boundary is
  introduced.

## Evidence locations

| Evidence | Location |
|---|---|
| HDL testbenches | [`hdl/tb/`](../../hdl/tb/) and [HDL Testbenches](hdl/testbenches.md) |
| Quartus build and STA | [`hdl/proj/`](../../hdl/proj/) and [Build Flow](hdl/build-flow.md) |
| Connected-board regression | [`host/app/hw_validation.py`](../../host/app/hw_validation.py) and [Hardware Validation](hardware-validation.md) |
| Backend and host tests | [`backend/app/tests/`](../../backend/app/tests/) and [`host/driver/tests/`](../../host/driver/tests/) |
| Frontend E2E evidence | [`frontend/tests/e2e/`](../../frontend/tests/e2e/) and [Frontend Build & Test](frontend/build-and-test.md) |
