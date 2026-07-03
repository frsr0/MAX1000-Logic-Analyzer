library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Run-length readback compressor (lossless).
--
-- Encodes a stream of 16-bit sample words as (count, value) pairs: each run of
-- identical words emits two output words, count first then value. A 512-sample
-- block therefore emits 2 words per distinct run. Idle/slow digital data (few
-- runs) compresses enormously; an incompressible block (up to 512 runs) would
-- emit more words than the raw block, but the OLS_Interface drain clamps the
-- response at BLOCK_SAMPLES words, so such a block comes back truncated and the
-- host re-reads it raw (decode yields < 512 samples -> raw fallback).
--
-- Unlike the delta codec this replaced, RLE is exact: there is no lossy
-- overflow/keyframe path, so a fully-emitted block always round-trips
-- bit-for-bit, and the compressed-vs-raw ambiguity is resolved purely by the
-- decoded sample count.
--
-- Block boundaries are external: the drain feeds exactly BLOCK_SAMPLES words
-- then asserts flush for one-or-more cycles to emit the final pending run. rst
-- (pulsed per block before feeding) starts a fresh run.
--
-- Passthrough (compression_enable = '0'): comp_data <= sample_in;
-- comp_valid <= sample_valid (raw, one word per sample).
--
-- Pacing: emitting a run takes two cycles (count, then value) during which no
-- input can be accepted, so in_ready is deasserted; the paced feeder (the
-- drain) must hold sample_valid low while in_ready is low.
entity rle_compressor is
  port (
    clk                : in  std_logic;
    rst                : in  std_logic;
    sample_in          : in  std_logic_vector(15 downto 0);
    sample_valid       : in  std_logic;
    compression_enable : in  std_logic;
    flush              : in  std_logic;  -- emit the pending run (block end)
    comp_data          : out std_logic_vector(15 downto 0) := (others => '0');
    comp_valid         : out std_logic := '0';
    busy               : out std_logic := '0';
    in_ready           : out std_logic := '1'
  );
end rle_compressor;

architecture rtl of rle_compressor is
  type state_t is (PASSTHROUGH, RUN, EMIT_CNT, EMIT_VAL);
  signal state : state_t := PASSTHROUGH;

  signal prev     : std_logic_vector(15 downto 0) := (others => '0');
  signal cnt      : unsigned(15 downto 0) := (others => '0');
  signal have     : std_logic := '0';  -- a run is in progress
  signal pend_val : std_logic_vector(15 downto 0) := (others => '0');
  signal pend_new : std_logic_vector(15 downto 0) := (others => '0');
  signal pend_flush : std_logic := '0';  -- emit is a flush (no new run after)
begin
  -- Busy whenever a run is held (unflushed) or an emit is in flight, so the
  -- drain's flush-wait does not complete until the final run is out.
  busy <= '1' when state = EMIT_CNT or state = EMIT_VAL or have = '1'
          else '0';
  -- Cannot accept input during the two emit cycles.
  in_ready <= '0' when state = EMIT_CNT or state = EMIT_VAL else '1';

  process(clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= PASSTHROUGH;
        comp_valid <= '0';
        have <= '0';
        cnt <= (others => '0');
        pend_flush <= '0';
      else
        comp_valid <= '0';  -- default

        case state is

          when PASSTHROUGH =>
            comp_data <= sample_in;
            comp_valid <= sample_valid;
            have <= '0';
            if compression_enable = '1' then
              state <= RUN;
            end if;

          when RUN =>
            if flush = '1' and have = '1' then
              -- Block end: emit the final (count, value).
              pend_val <= prev;
              pend_flush <= '1';
              state <= EMIT_CNT;
            elsif compression_enable = '0' then
              -- Compression turned off between blocks: revert to raw.
              state <= PASSTHROUGH;
              comp_data <= sample_in;
              comp_valid <= sample_valid;
            elsif sample_valid = '1' then
              if have = '0' then
                prev <= sample_in;
                cnt  <= to_unsigned(1, 16);
                have <= '1';
              elsif sample_in = prev then
                cnt <= cnt + 1;
              else
                -- Run ends: emit (cnt, prev); the new sample starts the next
                -- run. Hold it until the two emit cycles finish.
                pend_val   <= prev;
                pend_new   <= sample_in;
                pend_flush <= '0';
                state <= EMIT_CNT;
              end if;
            end if;

          when EMIT_CNT =>
            comp_data  <= std_logic_vector(cnt);
            comp_valid <= '1';
            state <= EMIT_VAL;

          when EMIT_VAL =>
            comp_data  <= pend_val;
            comp_valid <= '1';
            if pend_flush = '1' then
              have <= '0';
              pend_flush <= '0';
              state <= RUN;
            else
              prev <= pend_new;
              cnt  <= to_unsigned(1, 16);
              have <= '1';
              state <= RUN;
            end if;

        end case;
      end if;
    end if;
  end process;

end rtl;
