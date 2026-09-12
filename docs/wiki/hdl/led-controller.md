# LED Controller: `LED_Controller`

**File:** `hdl/rtl/LED_Controller.vhd`

## Purpose

Drives the complete eight-LED board array as a low-speed visual status display.
The controller uses only system-clock control signals, so it is isolated from
the timing-sensitive capture and SDRAM clock domains. It directly drives the
LED pins rather than using PWM: the MAX 10 is nearly full and the direct design
keeps the activity display small enough to fit alongside the capture engine.

## Activity patterns

| Priority | State | Pattern |
|---:|---|---|
| 1 | Capture complete | All eight LEDs flash together three times. |
| 2 | Generator receive | A three-LED packet travels right-to-left; each received byte is held visibly. |
| 3 | Generator transmit | A three-LED packet travels left-to-right for the active burst. |
| 4 | Live/continuous capture | Two opposing bright heads rotate with dim tails. |
| 5 | Finite capture | A bright two-wide scanner sweeps across the array. |
| 6 | Pre-trigger | Alternating four-LED banks show that the trigger is armed and awaited. |
| 7 | Idle | A slow dim comet travels over the whole array. |

The generator animations are the physical FPGA equivalents of bus TX/RX. The
current MIL service is host-side emulation, so it does not expose a separate
MIL hardware signal; when it is backed by the board generator, these are the
indicators it uses.

## Inputs

| Input | Meaning |
|---|---|
| `armed` | Capture is armed; with `capture_run=0`, select pre-trigger. |
| `capture_run` | Trigger fired and the capture engine is running. |
| `capture_full` | A finite capture completed. |
| `continuous_mode` | Distinguishes a live ring from a finite capture. |
| `generator_tx` | Bit Engine is actively transmitting. |
| `generator_rx` | One-clock pulse when Bit Engine RX FIFO occupancy grows. |

## Timing

A single 26-bit system-clock counter controls both the animation position
(about 84 ms per frame at 100 MHz) and the flash phase. RX events are held for
seven frames so a short received byte remains visible.

## Testing

`hdl/tb/tb_led_controller.vhd` self-checks idle, pre-trigger, finite capture,
live capture, TX, RX, and capture-complete priority/patterns.
