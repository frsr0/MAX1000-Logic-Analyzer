# Streaming RLE Protocol Plan

## Implementation Status (2026-07-03)

**Implemented and sim/unit-test validated. Hardware benchmark pending.**

- FPGA (`OLS_Interface.vhd`): `CMD_START_RAW_STREAM` gains an RLE subpath
  (`raw_stream_comp_mode`) selected when `rle_enable && !analog`. A
  FETCH→WAIT→FEED sequencer streams SDRAM words through `rle_compressor` into a
  16-byte output byte FIFO drained straight to the SPI shifter — no `block_buf`
  / `WAIT_BLOCK`. Terminates after exactly `raw_blk_rd_count_cfg` source
  samples, flushes, then returns to IDLE. Raw mode is untouched (all raw-path
  edits guarded by `raw_stream_comp_mode = '0'`).
  - Bug fixed during bring-up: the read FSM fed `Rd_Fifo_Q` one cycle too early
    (FIFO latency is 2 cycles, per the proven block-read drain), injecting a
    stale `0x0000` sample and dropping the last real one. FETCH/WAIT/FEED now
    mirrors the block-read timing.
  - Output FIFO deepened 8→16 with a `DEPTH-8` feed gate so a worst-case
    (run-length-1, 4 bytes/sample) in-flight feed can never overflow the store.
- Host (`spi_protocol.py`, `ols_spi.py`): `start_rle_stream_read` reads until
  `sample_count` samples *decode* (not a fixed byte budget) via the new
  `OLS.stream_command_chunks` CS-held generator — stops early on compressible
  data and never truncates incompressible data. Incremental decoder
  (`_decode_rle_into`) tolerates pairs split across chunk boundaries. A
  fixed-budget `stream_command` fallback remains for backends without chunking.
- `stream_ring_capture` routes rle codec → `start_rle_stream_read`.

Verification:
- HDL: `tb_ols_rle_raw_stream.vhd` (GHDL) passes for compressible, multi-run,
  and worst-case run-length-1 patterns (e.g. 128 samples → 128 exact pairs);
  `tb_batched_reads` still passes (block-read regression).
- Host: `pytest host/driver/tests host/tests` → 378 passing, incl. chunk-path
  ack parse, early-stop/CS-raise, chunk-boundary split, zero-count, overrun,
  truncation, and fixed-fallback cases.

Remaining: on-hardware bit-exact + throughput sweep (Test Plan → Hardware
validation, and Success Criteria's measured ceiling).

## Goal

Raise live-mode sample-rate ceiling by moving lossless digital compression onto
the existing CS-held `CMD_START_RAW_STREAM` wire path, instead of continuing to
pay the per-block `CMD_READ_CAPTURE` framing overhead and fixed response-slot
padding.

This document turns that direction into an implementation target.

## Current State

### Efficient path that already exists

- Host `start_raw_stream_read()` sends `CMD_START_RAW_STREAM`, keeps CS low, and
  then clocks sample bytes continuously under one transaction.
- FPGA `CMD_START_RAW_STREAM` enters the dedicated `RAW_STREAM` state after the
  packet ack and shifts bytes directly via `raw_stream_tx_byte`.

Relevant code:

- [host/driver/spi_protocol.py](C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/host/driver/spi_protocol.py)
- [hdl/rtl/OLS_Interface.vhd](C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/hdl/rtl/OLS_Interface.vhd)

### Compressed path that exists today

- Digital RLE compression is implemented in `rle_compressor.vhd`.
- Compressed readback still drains through the block-read path:
  `CMD_READ_CAPTURE` -> `WAIT_BLOCK` -> `block_buf` -> packet response payload.
- Host compressed batching assumes a fixed `430`-byte slot per block even though
  actual RLE size depends on data content.
- Incompressible blocks can exceed the slot budget, so the current design
  truncates them and relies on a raw fallback retry.

Relevant code:

- [hdl/rtl/rle_compressor.vhd](C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/hdl/rtl/rle_compressor.vhd)
- [host/driver/spi_protocol.py](C:/Users/Fraser/Documents/GitHub/OLS_Logic_Analyzer_Clean/host/driver/spi_protocol.py)

## Recommendation

Implement **streaming RLE over `CMD_START_RAW_STREAM`**.

Do not invest first in variable-slot block reads unless we want a smaller
interim optimization. Variable-slot block reads preserve the old request/ack
cadence and keep us coupled to the `WAIT_BLOCK` response assembler. Streaming
RLE removes both:

- the per-block request/response framing cost
- the fixed compressed-slot padding cost

## Core Design

### New mode semantics

Keep `CMD_START_RAW_STREAM` as the transport opcode, but let the payload format
depend on the selected readback codec:

- `compress_mode = raw`: stream little-endian 16-bit samples exactly as today
- `compress_mode = rle`: stream little-endian 16-bit `(count, value)` word
  pairs

The ack packet remains unchanged:

- status: `ST_STREAM_ACTIVE`
- payload: `producer_index`, `oldest_index`

This preserves the ring-metadata contract and avoids changing host resync
behavior.

### Framing rule

The host must request a **sample count**, not a byte count of compressed data.

For an RLE stream request of `N` samples:

- FPGA reads exactly `N` raw samples from SDRAM
- FPGA RLE-encodes them
- FPGA streams all resulting `(count, value)` pairs
- FPGA terminates the stream exactly when the encoded representation of those
  `N` samples is complete

This solves the current fixed-size/truncation problem. The endpoint is defined
by source-sample count, not guessed wire length.

### Why this framing is safe

RLE output is variable-rate, but it is still self-delimiting if we count decoded
samples on the host:

- each pair contributes `count` decoded samples
- the host stops once the running decoded sample total reaches `N`
- if the total would exceed `N`, treat it as protocol corruption

No extra per-run marker is needed.

## FPGA Changes

### 1. Add a compressed raw-stream subpath

Extend the existing `CMD_START_RAW_STREAM` handling in
`OLS_Interface.vhd` so it can stream either:

- raw sample words directly from the current `raw_word` fetch path
- RLE words from a new compressed stream pump

Suggested shape:

- keep the current raw path for `compress_mode = "00"`
- add a new internal mode for `compress_mode = "10"`
- reject legacy delta mode explicitly or alias it to raw for now

### 2. Reuse `rle_compressor`, but not `block_buf`

Do not run streaming RLE through `block_buf`, `blk_rsp_words`, or `WAIT_BLOCK`.

Instead:

- feed fetched SDRAM words into `rle_compressor`
- consume `comp_valid` output words directly in the `RAW_STREAM` byte shifter
- count source samples consumed and stop after `raw_blk_rd_count_cfg`
- keep draining compressor output until `busy = '0'`

That turns the compressor into a true source-to-wire streaming stage.

### 3. Add source-sample and output-drain bookkeeping

New internal counters/flags are needed:

- `raw_src_samples_rem`
- `raw_rle_active`
- `raw_rle_flush_pending`
- `raw_rle_have_word`
- `raw_rle_word`

Termination condition for RLE mode:

- all requested source samples have been fed into the compressor
- compressor flush has been issued
- compressor `busy = '0'`
- no partially shifted output word remains

### 4. Preserve current raw-stream ack behavior

The transition should still be:

- parse packet
- send normal packet ack
- after `pkt_tx_done`, enter byte-stream state

That keeps the host-side `ack_pad` and response parsing model intact.

## Host Changes

### 1. Add a dedicated `start_rle_stream_read()`

Do not overload `start_raw_stream_read()` with two incompatible return shapes.

Add a sibling method that:

- sends `CMD_START_RAW_STREAM`
- parses the normal ack
- continues clocking bytes until it has decoded the requested sample count
- returns decoded raw sample bytes to the caller

Suggested API:

```python
def start_rle_stream_read(
    self,
    start_sample: int,
    sample_count: int,
    stop_evt=None,
    ack_pad: int | None = None,
) -> tuple[int, int, bytes]:
    ...
```

Return value mirrors `start_raw_stream_read()`:

- `producer_index`
- `oldest_index`
- decoded little-endian raw sample bytes

This keeps `stream_ring_capture()` simple.

### 2. Teach `stream_ring_capture()` to use the new path

Current behavior:

- raw mode -> `start_raw_stream_read()`
- compressed mode -> `read_capture_range()` block reads

Target behavior:

- raw mode -> `start_raw_stream_read()`
- rle mode -> `start_rle_stream_read()`

Then the higher layers still receive ordinary raw sample bytes regardless of
transport.

### 3. Add a bounded read loop

Because compressed wire length is not known up front, host SPI support needs an
incremental read loop rather than one fixed `stream_command(..., n_bytes=...)`
call.

Two viable options:

- extend `OLS.stream_command()` to support chunked polling under held CS
- add a new low-level `stream_command_until(predicate)` helper

The second option is cleaner because raw and RLE paths have different stop
conditions.

## Low-Level FTDI/Transport Change

This is the one non-trivial host transport change.

Today `stream_command()` requires a known `n_bytes` target before the transfer
starts. Streaming RLE needs CS to remain low until the host has decoded enough
output to cover the requested sample count.

Recommended helper:

```python
def stream_command_chunks(self, request, ack_pad=96, chunk_bytes=4096):
    """Yield raw bytes from one CS-held request+stream transaction."""
```

Behavior:

- send request once
- clock `ack_pad` guard bytes
- repeatedly clock `chunk_bytes` of `0x11`
- yield received bytes incrementally
- caller decides when enough decoded samples were received
- then terminate by raising CS

This keeps the wire path generic and lets the higher-level packet code own the
RLE decode/stop rule.

## RLE Decode Contract

Host-side decoder for the stream:

- consume 16-bit words little-endian
- interpret as alternating `count`, `value`
- require `count >= 1`
- append `value` repeated `count` times
- stop once decoded sample total equals requested `sample_count`

Hard errors:

- odd byte count at end of transaction
- zero count
- decoded total exceeds requested total
- stream ends before requested total is reached

## Why Not Variable-Slot Block Reads First

Variable-slot block reads help less and cost more complexity than they appear.

They still require:

- one logical request per block
- one response header per block
- one dispatcher wakeup per block
- a host parser that can stay synchronized across mixed-size responses

They also do not remove the `WAIT_BLOCK`/`block_buf` staging path, which is
precisely the machinery we want to get off the hot path in live mode.

They are still a valid fallback project if we want a narrow host-side
optimization without changing `CMD_START_RAW_STREAM`, but they should not be the
primary path forward.

## Proposed Implementation Order

1. Add a low-level CS-held chunked stream reader on the host.
2. Add host-side RLE stream decode with unit tests.
3. Extend `CMD_START_RAW_STREAM` to support RLE mode in FPGA.
4. Keep existing raw-stream mode untouched.
5. Switch `stream_ring_capture()` to use RLE streaming when codec is `rle`.
6. Benchmark live throughput and verify round-trip integrity.

## Test Plan

### Host unit tests

- decode a normal `(count, value)` stream into exact sample bytes
- reject zero-count pairs
- reject overrun beyond requested sample count
- handle chunk boundaries splitting words/pairs

### HDL simulation

- fixed-pattern stream: long idle run, alternating run, worst-case run length 1
- verify exactly `N` source samples consumed
- verify raw-stream state exits only after final RLE words drain
- verify raw mode behavior unchanged

### Hardware validation

- bit-exact compare: streamed RLE decode vs streamed raw samples for same window
- long continuous run with static line, UART waveform, and high-toggle pattern
- throughput sweep to identify new live-mode ceiling

## Success Criteria

- live digital narrow mode uses `CMD_START_RAW_STREAM` for both raw and RLE
- no fixed `430`-byte compressed block slots on the hot path
- no compressed-block truncation + raw retry fallback in live mode
- decoded output remains bit-identical to raw captures
- measured live sample-rate ceiling increases beyond the current raw-stream-only
  limit
