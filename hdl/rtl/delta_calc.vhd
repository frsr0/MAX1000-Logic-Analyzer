library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- delta_calc : per-channel signed delta + block bit-width analyser (pipelined)
-- ---------------------------------------------------------------------------
-- Front end of the analog bit-packing pipeline. Samples arrive one at a time,
-- tagged with the source ADC channel (0..3), in the natural interleaved order
-- produced by the ADC_Controller round-robin. For each sample this block:
--
--   1. subtracts the previous sample *of that same channel* to form a signed
--      delta (adjacent-sample difference), saturated to signed 11 bits, and
--   2. tracks the maximum signed bit-width required by any delta over a window
--      of BLOCK_SAMPLES (16) samples, so the downstream packer knows how many
--      bits per sample it must reserve for the whole block.
--
-- The per-channel "previous sample" registers persist across block boundaries,
-- so the delta stream is continuous (no per-block anchor word).
--
-- Pipelining (3 stages) for the 200.4 MHz FAST_CLK domain
-- -------------------------------------------------------
-- A silicon-accurate fit on the 10M08 (C8) showed the flat single-cycle path
-- channel-mux -> 13-bit subtract -> saturate -> width priority-scan -> max-fold
-- at ~12.6 ns (~79 MHz). It is split across three register stages, each a short
-- slice of that chain:
--   S1: select prev(ch), 13-bit subtract  -> diff
--   S2: saturate(diff) -> delta   ||  req_width(diff) -> width   (parallel)
--   S3: fold width into the running block max; emit delta + block result
-- delta_out and block_width/block_done are produced together in S3, so the
-- packer's "block_done coincident with the last delta" contract is preserved;
-- only the input->output latency grows (by 2 cycles), which is immaterial at
-- ADC sample rates. Synchronous reset, single clock enable, no comb loops.
--
-- Delta range note: two 12-bit samples differ by at most +/-4095 (13 signed
-- bits); the format defines an 11-bit signed delta, so large transients
-- saturate to [-1024, 1023] (width 11). Adjacent ADC samples move slowly, so
-- this only bounds worst-case amplitude error.
entity delta_calc is
  generic (
    BLOCK_SAMPLES : positive := 16   -- samples per packed block (4 ch x 4)
  );
  port (
    clk          : in  std_logic;
    rst          : in  std_logic;                       -- synchronous, active high
    clk_en       : in  std_logic := '1';                -- global clock enable
    sample_in    : in  std_logic_vector(11 downto 0);   -- raw 12-bit ADC code
    sample_ch    : in  std_logic_vector(1 downto 0);    -- source channel 0..3
    sample_valid : in  std_logic;                       -- strobe: sample_in valid

    delta_out    : out std_logic_vector(10 downto 0) := (others => '0'); -- signed 11-bit
    delta_valid  : out std_logic := '0';                -- delta_out valid this cycle
    block_width  : out std_logic_vector(3 downto 0) := (others => '0');  -- max bits/sample 1..11
    block_done   : out std_logic := '0'                 -- pulses with the last delta of a block
  );
end delta_calc;

architecture rtl of delta_calc is

  -- One "previous sample" register per ADC channel.
  type prev_array is array(0 to 3) of unsigned(11 downto 0);
  signal prev : prev_array := (others => (others => '0'));

  signal count   : natural range 0 to BLOCK_SAMPLES-1 := 0;  -- samples into current block
  signal run_max : unsigned(3 downto 0) := (others => '0');  -- running max width (S3)

  -- S1 -> S2 pipeline registers
  signal diff1   : signed(12 downto 0) := (others => '0');
  signal v1      : std_logic := '0';
  signal first1  : std_logic := '0';
  signal last1   : std_logic := '0';

  -- S2 -> S3 pipeline registers
  signal dsat2   : std_logic_vector(10 downto 0) := (others => '0');
  signal w2      : unsigned(3 downto 0) := (others => '0');
  signal v2      : std_logic := '0';
  signal first2  : std_logic := '0';
  signal last2   : std_logic := '0';

  -- Minimum signed bit-width (1..11) needed to hold the 13-bit delta once
  -- saturated to 11 bits. Fixed 12-iteration priority scan from the MSB down
  -- (a static LUT tree), capped at 11 so an out-of-range transient maps to the
  -- saturated value's width. Result equals req_width of the saturated delta.
  function req_width_c(d : signed(12 downto 0)) return unsigned is
    variable w : unsigned(3 downto 0) := to_unsigned(1, 4);
    variable s : std_logic := d(12);   -- sign bit
  begin
    for i in 11 downto 0 loop
      if d(i) /= s then
        if i + 2 > 11 then
          w := to_unsigned(11, 4);
        else
          w := to_unsigned(i + 2, 4);
        end if;
        exit;
      end if;
    end loop;
    return w;
  end function;

begin

  process(clk)
    variable ch   : integer range 0 to 3;
    variable wmax : unsigned(3 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        prev        <= (others => (others => '0'));
        count       <= 0;
        run_max     <= (others => '0');
        v1 <= '0'; v2 <= '0';
        delta_valid <= '0';
        block_done  <= '0';
        delta_out   <= (others => '0');
        block_width <= (others => '0');
      elsif clk_en = '1' then

        -- ---------------- Stage 1: channel-relative subtract ----------------
        if sample_valid = '1' then
          ch    := to_integer(unsigned(sample_ch));
          diff1 <= signed(resize(unsigned(sample_in), 13))
                   - signed(resize(prev(ch), 13));
          prev(ch) <= unsigned(sample_in);
          v1    <= '1';
          if count = 0 then first1 <= '1'; else first1 <= '0'; end if;
          if count = BLOCK_SAMPLES - 1 then last1 <= '1'; else last1 <= '0'; end if;
          if count = BLOCK_SAMPLES - 1 then count <= 0; else count <= count + 1; end if;
        else
          v1 <= '0';
        end if;

        -- ------- Stage 2: saturate and width, in parallel from diff1 --------
        if v1 = '1' then
          if diff1 > 1023 then
            dsat2 <= std_logic_vector(to_signed(1023, 11));
          elsif diff1 < -1024 then
            dsat2 <= std_logic_vector(to_signed(-1024, 11));
          else
            dsat2 <= std_logic_vector(diff1(10 downto 0));
          end if;
          w2     <= req_width_c(diff1);
          v2     <= '1';
          first2 <= first1;
          last2  <= last1;
        else
          v2 <= '0';
        end if;

        -- ---------------- Stage 3: fold block max, emit ---------------------
        if v2 = '1' then
          delta_out   <= dsat2;
          delta_valid <= '1';

          if first2 = '1' then
            wmax := w2;                        -- first sample resets the window
          elsif w2 > run_max then
            wmax := w2;
          else
            wmax := run_max;
          end if;

          if last2 = '1' then
            block_width <= std_logic_vector(wmax);
            block_done  <= '1';
            run_max     <= (others => '0');
          else
            run_max     <= wmax;
            block_done  <= '0';
          end if;
        else
          delta_valid <= '0';
          block_done  <= '0';
        end if;

      end if;
    end if;
  end process;

end rtl;
