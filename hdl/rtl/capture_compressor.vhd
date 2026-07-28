library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Delta-packing compressor: 16 samples -> 6 words (2.67x compression).
-- Word 0: verbatim anchor.
-- Words 1..5: each packs 3 deltas (5-bit signed):
--   bits 4:0 = d0, bits 9:5 = d1, bits 14:10 = d2, bit 15 = 0.
-- Overflow (|delta| > 15): the fixed six-word format cannot represent all
-- fifteen deltas losslessly, so the block is marked invalid by suppressing
-- its packed payload. The host detects the short decode and retries raw.
-- Passthrough (compression_enable = '0'): comp_data <= sample_in;
-- comp_valid <= sample_valid.
entity capture_compressor is
  port (
    clk               : in  std_logic;
    rst               : in  std_logic;
    sample_in         : in  std_logic_vector(15 downto 0);
    sample_valid      : in  std_logic;
    compression_enable : in  std_logic;
    comp_data         : out std_logic_vector(15 downto 0) := (others => '0');
    comp_valid        : out std_logic := '0';
    busy              : out std_logic := '0';
    -- '0' while flushing packed delta words: sample_valid pulses asserted in
    -- FLUSH are silently dropped, so a paced feeder (the OLS_Interface block
    -- drain) must hold input while in_ready is low.
    in_ready          : out std_logic := '1'
  );
end capture_compressor;

architecture rtl of capture_compressor is
  type state_t is (PASSTHROUGH, ANCHOR, ACCUM, FLUSH);
  signal state : state_t := PASSTHROUGH;

  -- Accumulated deltas (up to 15 per block)
  -- Store three 5-bit deltas per word.  The previous implementation kept 15
  -- separate entries and performed three indexed array reads for every flush
  -- word, creating three mux trees.  The packed form keeps the same 75 bits
  -- but reduces flush to one indexed 15-bit read.
  type packed_delta_array is array(0 to 4) of std_logic_vector(14 downto 0);
  signal packed_deltas : packed_delta_array := (others => (others => '0'));
  signal delta_cnt : natural range 0 to 15 := 0;
  signal sample_cnt : natural range 0 to 15 := 0;
  signal prev : signed(15 downto 0) := (others => '0');
  signal overflow_flag : std_logic := '0';
  signal sample_pipe : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_pipe_valid : std_logic := '0';

  -- FLUSH output counter: which packed word (0..4) we are emitting
  signal out_idx : natural range 0 to 5 := 0;
begin
  busy <= '1' when state = ACCUM or state = FLUSH or sample_pipe_valid = '1'
          else '0';
  in_ready <= '0' when state = FLUSH else '1';

  process(clk)
    -- Difference of two 16-bit signed samples needs 17 bits before
    -- saturation; keeping it at 16 bits can wrap and evade the overflow test.
    variable delta_v : signed(16 downto 0);
    variable sat5   : std_logic_vector(4 downto 0);
    variable delta_overflow : boolean;
    variable flush_word : std_logic_vector(14 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= PASSTHROUGH;
        comp_valid <= '0';
        sample_pipe_valid <= '0';
      else
        comp_valid <= '0';  -- default

        case state is

          when PASSTHROUGH =>
            sample_pipe_valid <= '0';
            if compression_enable = '1' then
              state <= ANCHOR;
              sample_cnt <= 0;
              delta_cnt <= 0;
              overflow_flag <= '0';
              -- Capture the first sample as the anchor. Passing it through
              -- here would prepend a raw word to the compressed stream.
              if sample_valid = '1' then
                comp_data <= sample_in;
                comp_valid <= '1';
                prev <= signed(sample_in);
                sample_cnt <= 1;
                state <= ACCUM;
              end if;
            else
              comp_data <= sample_in;
              comp_valid <= sample_valid;
            end if;

          -- ANCHOR: wait for the first sample, emit verbatim anchor
          when ANCHOR =>
            if sample_valid = '1' then
              comp_data <= sample_in;
              comp_valid <= '1';
              prev <= signed(sample_in);
              sample_cnt <= 1;
              delta_cnt <= 0;
              overflow_flag <= '0';
              sample_pipe_valid <= '0';
              state <= ACCUM;
            end if;

          -- ACCUM: collect samples 1..15, compute deltas, store in array
          when ACCUM =>
            if sample_valid = '1' then
              sample_pipe <= sample_in;
              sample_pipe_valid <= '1';
            elsif sample_pipe_valid = '1' then
              sample_pipe_valid <= '0';
            end if;

            if sample_pipe_valid = '1' then
              delta_v := resize(signed(sample_pipe), delta_v'length)
                         - resize(prev, delta_v'length);
              if delta_v < -15 then sat5 := "10001";
              elsif delta_v > 15 then sat5 := "01111";
              else sat5 := std_logic_vector(delta_v(4 downto 0));
              end if;
              delta_overflow := (delta_v < -15) or (delta_v > 15);
              case delta_cnt is
                when 0  => packed_deltas(0)(4 downto 0)   <= sat5;
                when 1  => packed_deltas(0)(9 downto 5)   <= sat5;
                when 2  => packed_deltas(0)(14 downto 10) <= sat5;
                when 3  => packed_deltas(1)(4 downto 0)   <= sat5;
                when 4  => packed_deltas(1)(9 downto 5)   <= sat5;
                when 5  => packed_deltas(1)(14 downto 10) <= sat5;
                when 6  => packed_deltas(2)(4 downto 0)   <= sat5;
                when 7  => packed_deltas(2)(9 downto 5)   <= sat5;
                when 8  => packed_deltas(2)(14 downto 10) <= sat5;
                when 9  => packed_deltas(3)(4 downto 0)   <= sat5;
                when 10 => packed_deltas(3)(9 downto 5)   <= sat5;
                when 11 => packed_deltas(3)(14 downto 10) <= sat5;
                when 12 => packed_deltas(4)(4 downto 0)   <= sat5;
                when 13 => packed_deltas(4)(9 downto 5)   <= sat5;
                when others => packed_deltas(4)(14 downto 10) <= sat5;
              end case;
              prev <= signed(sample_pipe);

              if delta_overflow then
                overflow_flag <= '1';
              end if;
              if sample_cnt = 15 then
                -- Block full: an overflowed group must not look like a valid
                -- six-word block. A keyframe cannot fit alongside all fifteen
                -- deltas, so suppress the payload and let the host retry raw.
                if (overflow_flag = '1') or delta_overflow then
                  delta_cnt <= 0;
                else
                  -- delta_cnt was not incremented for this sample (the 15th
                  -- delta is at index 14).
                  delta_cnt <= delta_cnt + 1;
                end if;
                state <= FLUSH;
                out_idx <= 0;
              else
                delta_cnt <= delta_cnt + 1;
                sample_cnt <= sample_cnt + 1;
              end if;
            end if;

          -- FLUSH: emit packed delta words. Each word carries 3 deltas.
          -- Words are independent; block completes after out_idx reaches
          -- the number needed for delta_cnt deltas.
          when FLUSH =>
            -- Read one pre-packed 15-bit delta word (if it exists).
            if out_idx * 3 < delta_cnt then
              flush_word := packed_deltas(out_idx);
              if out_idx * 3 + 1 >= delta_cnt then
                flush_word(9 downto 5) := (others => '0');
              end if;
              if out_idx * 3 + 2 >= delta_cnt then
                flush_word(14 downto 10) := (others => '0');
              end if;
              comp_data(14 downto 0) <= flush_word;
              comp_data(15) <= '0';
              comp_valid <= '1';
              out_idx <= out_idx + 1;
            else
              -- All deltas emitted; start next block
              state <= ANCHOR;
              sample_cnt <= 0;
              delta_cnt <= 0;
              overflow_flag <= '0';
              sample_pipe_valid <= '0';
            end if;

        end case;
      end if;
    end if;
  end process;

end rtl;
