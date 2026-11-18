# Compressed Streaming Blocker Analysis

## Current State

**Working:** Live uncompressed streaming (~1.55 MS/s)
**Broken:** Live compressed streaming (CMD_READ_STREAM_BLOCK with Compress_Enable=1 in continuous mode)

---

## Architecture Path Difference

### Uncompressed Live Streaming (WORKING)
```
START_STREAM cmd
  ↓
OLS_Interface.vhd line 1045:
  if prefetch_valid AND prefetch_addr==stream_addr AND compress_enable='0'
    ↓ YES → FAST PATH
    - Uses prefetch (pre-fetched data from capture)
    - Sets feeding_block := true immediately
    - Response length = BLOCK_SAMPLES * 2 (512 samples)
    - Goes straight to BUILD_RSP
```

### Compressed Live Streaming (BLOCKED)
```
START_STREAM cmd
  ↓
OLS_Interface.vhd line 1045:
  if prefetch_valid AND prefetch_addr==stream_addr AND compress_enable='0'
    ↓ NO (compress_enable='1') → SLOW PATH
    - Can't use prefetch
    - Issues block_rd_issue_req
    - Goes to WAIT_BLOCK state
    - Waits for block_rd_ack from FLA
    - Response length = 192 * 2 (6 compressed words)
    ↓
FLA Fast_Logic_Analyzer_SDRAM.vhd:
  - Compressor (capture_compressor.vhd) fed with s_rdata
  - compression_enable signal wired directly
  - Output: comp_rdata (16-bit) + comp_valid
  - Compressed data fed to Rd_Fifo
```

---

## Suspected Root Causes

### 1. Compressor State Machine in Continuous Mode ❌
**Problem:** Compressor has internal state (PASSTHROUGH → ANCHOR → ACCUM → FLUSH)

**Issue in continuous streaming:**
- Compressor state might not sync with block boundaries
- Each block = 16 samples → 6 compressed words
- If continuous streaming has different sample grouping, state breaks

**Code location:** `capture_compressor.vhd` lines 28-42 (state machine)

### 2. Missing Reset Between Blocks 🤔
**Problem:** compressor uses `rdfifo_aclr` as reset (line 1197)

**Possible issue:**
- rdfifo_aclr might not pulse between continuous streaming blocks
- Compressor state persists across block boundaries incorrectly
- Delta accumulation doesn't reset

### 3. Block Boundary Misalignment ⚠️
**Problem:** Line 1370 in OLS_Interface.vhd:
```vhdl
blk_rsp_words <= BLOCK_SAMPLES when compress_enable_i = '0' or streaming_active = '0' else 192;
```

**Issue in continuous mode:**
- Expects exactly 192 words (6 compressed words × 16 samples)
- But if SDRAM read timing differs, blocks might not align
- Compressor state machine might emit fewer words than expected

### 4. Streaming Protocol Incompatibility 🔴
**Problem:** Line 1230-1234 in OLS_Interface.vhd (WAIT_BLOCK state):
```vhdl
if block_rd_ack = '1' then
  rsp_len_v := blk_rsp_words * 2;  -- 192 * 2 for compressed, 512 * 2 for uncompressed
  feeding_block := true;
  ...
end if;
```

**Critical blocker:**
- `feeding_block := true` triggers host side to expect `blk_rsp_words * 2` bytes
- For compressed: expects 384 bytes (6 words * 2)
- But FLA/compressor might not deliver exactly 384 bytes in streaming mode
- Response size mismatch → host ack breaks, streaming stalls

---

## Why It Works for Single-Shot

**Single-shot CMD_READ_CAPTURE (uncompressed):**
- Uses different code path (line 1077-1089)
- Blocks are read completely, assembled into buffer
- Compressor state is reset between captures
- Block boundaries are guaranteed

**Streaming CMD_READ_STREAM_BLOCK (uncompressed):**
- Uses prefetch (fast path) - already has SDRAM data
- Completely bypasses FLA's block-read FSM
- Compressor is disabled (gated off line 1046)
- No state machine issues

**Streaming CMD_READ_STREAM_BLOCK (compressed):**
- Uses slow path (FLA block-read FSM)
- Compressor state machine active
- Continuous mode buffering complicates block boundaries
- **Response size mismatch possible**

---

## Diagnosis Needed

To fix, we need to determine:

1. **Does compressor emit correct word count?**
   - Add debug signal to count `comp_valid` pulses
   - Verify 6 words emitted per 16 input samples in streaming mode

2. **Is compressor state reset between blocks?**
   - Check if compressor FSM returns to PASSTHROUGH/ANCHOR correctly
   - Verify delta_cnt and sample_cnt reset

3. **Does FLA's block-read FSM handle compressed data?**
   - The block-read FSM (around line 1300+ in FLA) expects BLOCK_SAMPLES
   - But compressed mode changes the expected output size
   - May be dropping data or stalling

4. **Is the response size correct?**
   - blk_rsp_words = 192 for compressed (line 1370)
   - But does actual data delivered match?

---

## Next Steps to Debug

### Option A: Instrument the Code (Conservative)
Add debug signals to track:
```vhdl
signal comp_word_count : integer := 0;  -- Count comp_valid pulses
signal compressor_state_debug : state_t;  -- Export compressor state
signal fifo_write_count : integer := 0;  -- Count FIFO writes
```

Then use observation to see if counts match expectations.

### Option B: Verify Block Boundary Handling
Check if the FLA's block-read FSM (which manages SDRAM readout) correctly handles:
- compressed block size (192 words not 512)
- state machine transitions
- data handoff to FIFO

### Option C: Isolate to Compressor
Create a simple testbench with:
- 16 test samples fed to compressor
- Verify 6 words emitted
- Verify state machine cycles correctly
- Verify output matches expected compression ratio

---

## Likely Fix

Based on architecture, the issue is probably:

**The FLA's block-read FSM expects BLOCK_SAMPLES (512) words, but with compression it only gets 192.**

Fix would be:
1. In FLA block-read FSM, check `Compress_Enable` parameter
2. If compressed, expect 192 words instead of 512
3. Adjust the state machine transitions accordingly
4. Ensure compressor state resets correctly between blocks

**Location:** `Fast_Logic_Analyzer_SDRAM.vhd` around line 1300-1400 (block-read FSM main process)

---

## Verification Plan

Once fix is implemented:

1. **Unit test:** tb_stream_read_compressed.vhd
   - Simulate START_STREAM with Compress_Enable=1
   - Verify Rd_Fifo output is 192 words per 16 samples
   - Verify no data loss or stalls

2. **Integration test:** Live capture
   - Set OLS_EXPERIMENTAL_COMPRESSED_LIVE=1
   - Run live capture at various rates
   - Verify sample count matches expected (rate * time)
   - Verify no glitches or missing blocks

3. **Performance:** Measure MS/s
   - Compressed: expect ~1.55 * 2.67 ≈ **4.1 MS/s** (compressed ratio)
   - Or in raw sample rate: depends on transport limit (~3.2 MB/s ÷ 2 bytes/sample ÷ compression_ratio)
