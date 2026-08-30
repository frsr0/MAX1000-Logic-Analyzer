# ADC Controller: `ADC_Controller`

**File:** `hdl/rtl/ADC_Controller.vhd`

`ADC_Controller` adapts up to four requested MAX 10 ADC mux channels onto the
`altera_modular_adc_control` command/response interface. Capture-profile policy
lives in `OLS_SDRAM_Top`; the controller itself is an indexed round-robin
request sequencer.

## Interface

| Port group | Purpose |
|---|---|
| `sys_clk`, `adc_clk`, `reset` | Command FSM clock, 12 MHz conversion clock, reset |
| `ch0_sel`-`ch3_sel` | ADC mux indices, each 0-31 |
| `ch0_start`-`ch3_start` | Per-channel request bits |
| `ch0_result`-`ch3_result` | 12-bit conversion results |
| `ch0_valid`-`ch3_valid` | One-cycle result strobes |

After ADC initialization, the controller latches the requested-channel vector,
walks indices 0-3, issues one command for every asserted request, waits for the
response, stores it in the corresponding result slot, and continues to the
next requested channel.

## Active capture profiles

`OLS_SDRAM_Top` translates `REG_FLAGS` into request/select behavior:

| Product profile | ADC behavior | User-facing rate |
|---|---|---:|
| Analog fast | Request one selected mux channel | 100 kS/s-1 MS/s |
| Mixed scan | Request two ADC results alongside 16 digital inputs | about 125 kframes/s |
| Maximum analog | Packed MSO mode requests ADC1-ADC4 in round robin | about 24 kS/s per physical lane |

The maximum-analog backend deliberately uses packed MSO output. The legacy
raw `MODE_ANALOG_ALL` frame path did not represent eight independent physical
lanes and is not the product contract.

## MAX1000 physical inputs

| ADC mux | Board label | Header |
|---:|---|---|
| 1 | AIN3 | J1/5 |
| 2 | AIN1 | J1/3 |
| 3 | AIN4 | J1/6 |
| 4 | AIN6 | J1/8 |

Other MAX1000 mux inputs are represented in board metadata where physically
available, but the current four-lane packed product profile is ADC1-ADC4.

## Clocking and framing

The MAX 10 ADC hard IP receives a 12 MHz conversion clock from PLL c3. The
top-level capture logic converts valid strobes into either a single-lane raw
frame, mixed framing, or packed four-lane MSO blocks. ADC codes are converted
to volts in the host/backend with a 3.3 V, 12-bit scale.

## Verification

- `tb_adc_controller.vhd` is in the maintained GHDL gate and checks request
  ordering, channel selection, and result strobes.
- Packed four-lane behavior is covered by the MSO simulations and the
  connected-board maximum-analog single/live matrix.
- `tb_analog_preamble.vhd` is an explicit expected failure in the current
  GHDL harness; it is not counted as a pass.

See [MSO Capture](mso-capture.md), [Capture Strategies](../backend/capture-strategies.md),
and [Hardware Validation](../hardware-validation.md).
