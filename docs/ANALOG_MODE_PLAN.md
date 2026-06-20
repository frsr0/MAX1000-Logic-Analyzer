# MAX1000 Analog And Mixed Mode Plan

This note separates the capture modes and the MAX1000 ADC mux mapping. The RTL
now has explicit profiles for mixed, high-speed analog, and maximum analog;
Quartus fitting and on-hardware timing have been run on the current bitstream.
The ADC hard-IP path uses a 12 MHz conversion clock with `clkdiv=1`.

## Current Implementation

The FPGA has three ADC scan profiles:

| Path | RTL behavior | Host behavior | Status |
|---|---|---|---|
| Digital-only | 2-byte frames, 16 digital channels | Used for digital single/continuous/rolling captures | Good; reaches full digital rate |
| Narrow digital | 2-byte packed words; one selected digital channel, 16 time samples per word | Used for 200 MHz narrow rolling mode | Works in finite and continuous hardware validation |
| Mixed | 14-byte frames: 16 digital bits plus ADC0-ADC7 packed as eight 12-bit values | Used for mixed mode | Works at 125 kframes/s; digital is sampled once per ADC frame |
| High-speed analog | 2-byte frames: one selected 12-bit ADC mux result | Used for high-speed analog mode | Works at 1 MSPS; default host selection is ADC1/AIN3 |
| Maximum analog | 12-byte frames: ADC1,2,3,4,5,7,8,16 packed as eight 12-bit values | Used for maximum analog mode | Works at 125 kframes/s |

Board-guide mapping is not a linear `AIN0..AIN7` sequence. Mixed mode still
contains two unmapped mux slots, but maximum analog scans the documented
physical analog profile:

| Board pin | ADC mux channel | Mixed ADC0-ADC7 | Maximum analog |
|---|---:|---|---|
| AIN1 | ADC2 | Yes | Yes |
| AIN2 | ADC5 | Yes | Yes |
| AIN3 | ADC1 | Yes | Yes |
| AIN4 | ADC3 | Yes | Yes |
| AIN5 | ADC7 | Yes | Yes |
| AIN6 | ADC4 | Yes | Yes |
| AIN0 | ADC8 | No | Yes |
| AIN | ADC16 | No | Yes |
| AIN7 | needs verification | No | No |
| AREF | reference | No | No |

The original root cause was RTL selection, not the frontend. That is now
addressed by widening ADC selections to 0-31, adding ADC8 to the ADC IP mask,
threading `Analog_Profile` and `Analog_Channel` out of `REG_FLAGS`, and deriving
the frame boundary from the active profile instead of always using `adc7_valid`.

## Target Four Modes

These are the four main user-facing mixed-signal modes. The separate narrow
digital mode is a digital-only rolling optimization, not a fifth analog mode.

| User mode | Goal | Current support | Required fix |
|---|---|---|---|
| Full digital | 16 digital inputs at maximum digital speed, up to 200 MHz in speed builds | Supported | Keep existing digital path |
| Mixed | A mix of analog and digital at the best practical combined speed | Supported via 16 digital + ADC0-ADC7 frame at 125 kframes/s | Keep pin-map/noise validation current |
| High-speed analog | Maximum analog detail for one selected physical analog input | Implemented as a one-slot ADC profile | Add UI channel selector beyond default ADC1/AIN3 |
| Maximum analog | All verified physical analog inputs at best per-channel detail | Implemented as ADC1,2,3,4,5,7,8,16 profile at 125 kframes/s | Keep physical-input validation current |

## RTL Work

Implemented:

1. `REG_FLAGS` bit 4 selects analog-only framing.
2. `REG_FLAGS` bits 6:5 select the analog profile; profile `01` is maximum
   analog, profile `00` is high-speed analog when analog-only is set.
3. `REG_FLAGS` bits 12:8 select the ADC mux channel for high-speed analog.
4. ADC selections are widened to 0-31 and the ADC IP mask includes ADC8 and
   ADC16.
5. `OLS_SDRAM_Top` selects slots per profile and toggles the analog frame on
   `adc0_valid` for high-speed analog or `adc7_valid` for 8-slot profiles.
6. Frame formats are decoded by the host as 14-byte mixed, 2-byte high-speed
   analog, or 12-byte maximum analog.
7. Narrow digital uses `REG_FLAGS` bit 13 plus bits 17:14 for the selected
   digital channel and packs 16 consecutive time samples per word.

## Host And UI Work

Implemented:

1. Frontend mode choices expose mixed, high-speed analog, and maximum analog.
2. Backend API accepts `analog_fast` and `analog_all`.
3. Host decode supports variable analog frame layouts.
4. Captured analog channel IDs preserve mux identity, for example `a1`,
   `a8`, and `a16`.
5. Frontend and backend expose `digital_narrow` as a 200 MHz rolling option.

Remaining:

1. Add a frontend selector for the high-speed analog ADC/physical input.
2. Report bitstream profile metadata directly rather than relying only on host
   mode constants.

## Verification

Completed on the current bitstream:

1. Quartus timing closes for the 100/200 MHz speed build.
2. Hardware validation passes `564/564`, including mixed, high-speed analog,
   maximum analog, mixed/digital recovery, and 200 MHz narrow packed digital
   finite/continuous capture.
3. Rate measurement confirms about 1 MSPS for high-speed single-channel analog
   and about 125 kframes/s for mixed/maximum 8-input scan frames.

Still useful follow-up:

1. Hardware sweep with each physical analog input driven and all other analog
   inputs tied low, proving that no displayed physical channel is a floating
   unmapped mux slot.
2. Open-input and grounded-input tests to explain noise: floating ADC mux inputs
   and unconnected board pins will show noise unless the UI marks them unmapped
   or the RTL stops scanning them.
