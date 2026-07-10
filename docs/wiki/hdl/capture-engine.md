# Capture Engine: `Fast_Logic_Analyzer_SDRAM`

**File:** `hdl/rtl/Fast_Logic_Analyzer_SDRAM.vhd` (2033 lines)

## Purpose

The core capture engine: registered input sampling, sample rate division, BRAM pre-trigger buffer, async FIFO bridge to the SDRAM domain, SDRAM write pump, narrow packed digital mode, triple-buffer continuous/ring capture, and block readout via a CDC response FIFO.

## Entity Signature

### Generics

| Generic | Default | Description |
|---|---|---|
| `Max_Samples` | 4,194,304 | Maximum capture depth (SDRAM words) |
| `Channels` | 16 | Number of digital channels (1..16) |
| `Sim` | false | Simulation mode (faster completion countdown) |
| `FAST_SPEED` | false | Enable 200 MHz sample clock path |
| `FAST_RAW_BUILD` | true | Exclude compression at elaboration |
| `CLK_Frequency` | 100,000,000 | System clock |
| `SDRAM_CLK_HZ` | 166,666,667 | SDRAM clock |
| `SAMPLE_CLK_HZ` | 200,000,000 | Sample clock for BRAM depth calc |
| `Write_Latency` | 10 | SDRAM write latency (cycles) |
| `Read_Latency` | 3 | SDRAM read latency |
| `Page_Latency` | 3 | SDRAM page turnaround |

### Key Port Groups

**Control:** `CLK`, `SDRAM_CLK_IN`, `FAST_CLK`, `Rate_Div` (1..500M), `Samples` (1..Max_Samples), `Start_Offset`, `Run`, `Full`, `Armed`, `Fast_Mode`, `Continuous_Mode`, `Buffer_Full[2:0]`, `Buffer_Ack[2:0]`

**Data:** `Inputs[Channels-1:0]`, `Address` → `Outputs[15:0]`

**SDRAM:** `sdram_addr[11:0]`, `sdram_ba[1:0]`, `sdram_dq[15:0]`, `sdram_dqm[1:0]`, `sdram_cas_n`, `sdram_ras_n`, `sdram_we_n`, `sdram_cke`, `sdram_cs_n`, `sdram_clk`

**Analog/Packed:** `Analog_Frame_Data[127:0]`, `Analog_Frame_Len`, `Analog_Stream_Mode`, `Analog_Frame_Toggle`, `Packed_Mode`, `Packed_Data[15:0]`, `Packed_Valid`, `Packed_Ready`

**Block Readout:** `Blk_Rd_Req_Tog`, `Blk_Rd_Base`, `Blk_Rd_Count`, `Auto_Renew`, `Rd_Fifo_Q[15:0]`, `Rd_Fifo_Empty`, `Rd_Fifo_RdReq`

**Ring Metadata:** `Producer_Index[31:0]`, `Oldest_Index[31:0]`, `Newest_Index[31:0]`, `Overrun_Count[31:0]`

**Pump Diagnostics:** `Pump_Valid_Cycles[31:0]` through `Pump_Overflow_Count[31:0]` (disabled by `PUMP_METRICS=false` to save LEs)

## Clock Domains

| Clock | Domain | Role |
|---|---|---|
| `CLK` | System (100 MHz) | Configuration handshake, address readout |
| `SDRAM_CLK_IN` | SDRAM core (167 MHz) | Write pump, SDRAM controller, block readout CDC |
| `FAST_CLK` | Fast sample (200 MHz) | Input sampling, rate division, packing, analog frame capture |

## Internal Architecture

### 1. Input Sampling and Rate Division (FAST_CLK domain)

- `Inputs_r` — registered inputs at FAST_CLK
- `cnt_s` — down-counter from `rate_div_m1_f` (Rate_Div - 1), generates `sample_tick_r`
- `run_f_level` — sampled level of Run after 2-FF synchronisation
- `Fast_Mode` → `fast_mode_f` via 2-FF CDC
- Triple-buffer continuous mode uses `CONT_BUF=512` samples per buffer

### 2. Pre-trigger BRAM (FAST_CLK domain)

1024-word circular BRAM stores samples before trigger. In single-shot mode, samples flow through:
1. Rate-divider tick → sample capture → BRAM write
2. After `Start_Offset` samples past trigger, switch to SDRAM write
3. BRAM provides pre-trigger data for the first readout

### 3. Async FIFO Bridge

- `AFIFO_DEPTH=1024`, `AFIFO_WIDTH=16`
- Dual-clock FIFO (dcfifo IP): write side = FAST_CLK, read side = pclk (SDRAM_CLK_IN)
- `fifo_afull_r` — almost-full at ~320 words headroom (256-word cushion)
- Packed mode: `packed_mode_f` muxes the FIFO write source between analog frame writer and packed stream

### 4. SDRAM Write Pump (pclk / SDRAM_CLK_IN domain)

- Opens SDRAM page, streams continuous writes
- `producer_done_q` — asserted when the FAST_CLK sample budget is exhausted and FIFO is drained
- `single_drain_cnt` — counts empty pclk cycles after producer done (2047-cycle timeout = 12.3 µs at 167 MHz)
- Open-page policy keeps the row open for streaming writes
- `z_count_r` — the actual sample count written vs `Samples` configured

### 5. Triple-Buffer Continuous Mode

- `buf_sel[1:0]` — round-robin through 3 buffers of `CONT_BUF=512` samples each
- `buf_full[2:0]` — one-hot flags per buffer
- Producer fills the active buffer; when full, swaps to next and sets `Buffer_Full[buf_sel]`
- Host ACKs via `Buffer_Ack[buf_sel]` to recycle a buffer
- Ring: `ring_used` tracks total occupied words; oldest samples are overwritten at ring capacity (`CONT_RING_WORDS=Max_Samples`)

### 6. Narrow Digital Mode

- `Narrow_Enable` + `Narrow_Channel` select one digital channel
- Packs 16 time-samples per 16-bit word (1-bit per sample)
- Effective 200 MHz capture depth = SDRAM_WORDS × 16 = 67,108,864 logical samples

### 7. Block Readout (Response FIFO)

- `Blk_Rd_Req_Tog` toggle starts streaming from `Blk_Rd_Base` for `Blk_Rd_Count` samples
- Response FIFO (`Rd_Fifo_Q/Empty/RdReq`) is a true CDC dcfifo between pclk readout domain and CLK domain
- Replaces the old fixed-latency Address/Outputs latch that corrupted block boundaries
- `Auto_Renew` enables gapless streaming across block boundaries

### 8. Producer-Done Completion

- FAST_CLK: `producer_done_toggle_f` toggles when sample budget exhausted
- CLK side: detects toggle edge, enables drain completion counter
- Fixes the old hang where completion waited on an exact write count the producer never reached
- Validated 36/36 captures at full 4M depth across 18-200 MHz

## Key Signals

| Signal | Width | Description |
|---|---|---|
| `sample_tick_r` | 1 | One-cycle pulse at sample rate |
| `fifo_afull_r` | 1 | Almost-full gate for FAST_CLK producers |
| `producer_index_u` | 32 | Current write position in ring |
| `oldest_index_u` | 32 | Oldest retained sample in ring |
| `newest_index_u` | 32 | Newest captured sample in ring |
| `overrun_count_u` | 32 | Count of samples lost to overflow |
| `packed_mode_f` | 1 | Synchronised packed mode flag |
| `packed_stop_f` | 1 | End-of-capture gate for packed producer |

### Packed-Mode Capture Budget (fixed 2026-07-10)

`packed_stop_f` (`<= not sample_rem_nonzero_r`) gates `Packed_Ready` for the
MSO/packed producer (see [mso-capture.md](mso-capture.md)). Because
`mso_capture.vhd` has no `Rate_Div`/`sample_tick_r` gating of its own — it
ingests `digital_in` unconditionally every `fast_clk` cycle — the shared
`sample_remaining`/`sample_rem_nonzero_r` budget that `packed_stop_f` reads
must also deplete at that same full-rate cadence, not at the `Rate_Div`-gated
rate used by the plain digital/narrow/analog-frame producers. Before the fix,
it was accidentally tied to the legacy `sample_tick_r` pulse, so a *higher*
requested `rate_hz` (meaningless to packed mode, since the write-port mux
never routes plain-digital data when `packed_mode_f='1'`) burned the budget
*faster* in wall-clock time — capture got shorter the more aggressively a
caller asked for speed. Fixed by decrementing on every `fast_clk` cycle when
`packed_mode_f='1'` instead. This also required adding an explicit
`Continuous_Mode='1'` auto-renew (reload `sample_remaining <= cfg_samples`
instead of latching `packed_stop_f` permanently), since `Packed_Ready` — unlike
the plain digital path, whose `fifo_wr` is not gated by this flag — really did
stop forever once the budget hit zero. Regression: `tb_packed_continuous_renew.vhd`.

## Known Limitations

- Pump metric counters (`PUMP_METRICS=false`) are disabled by default to save ~200 LEs
- Triple-buffer ACK must arrive before buffer overrun in continuous mode
- Narrow digital: overflow when 16 samples accumulate before FIFO accepts

## Testing

| Testbench | What it covers |
|---|---|
| `tb_minimal_capture.vhd` | Basic capture arm → wait → readback |
| `tb_capture_path.vhd` | End-to-end capture through the datapath |
| `tb_fla_drop.vhd` | Sample-drop tests at the FLA |
| `tb_core_stream.vhd` | Core streaming readback |
| `tb_core_batched_reads.vhd` | Batched block readout |
| `tb_continuous_rate1.vhd` | Continuous capture at Rate_Div=1 |
| `tb_continuous.vhd` | Continuous/ring mode |
| `tb_continuous_wedge.vhd` | Continuous-mode hang recovery |
| `tb_flush_path.vhd` | FIFO flush path |
| `tb_packed_continuous_renew.vhd` | Packed-mode continuous capture budget auto-renew (2026-07-10 fix) |
