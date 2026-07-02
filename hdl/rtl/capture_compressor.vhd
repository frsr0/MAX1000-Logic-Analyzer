library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Delta-packing compressor: 16 samples -> 6 words (2.67x compression).
-- Word 0: verbatim anchor.
-- Words 1..5: each packs 3 deltas (5-bit signed):
--   bits 4:0 = d0, bits 9:5 = d1, bits 14:10 = d2, bit 15 = 0.
-- Overflow (|delta| > 15): the delta is saturated to +/-15, and an
-- overflow flag causes the current block to emit a verbatim-reset word
-- (bit 15=1, low 15 bits = new anchor) immediately after the anchor,
-- then resume normal delta packing.
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
    busy              : out std_logic := '0'
  );
end capture_compressor;

architecture rtl of capture_compressor is
  type state_t is (PASSTHROUGH, ANCHOR, ACCUM, FLUSH);
  signal state : state_t := PASSTHROUGH;

  -- Accumulated deltas (up to 15 per block)
  type delta_array is array(0 to 14) of std_logic_vector(4 downto 0);
  signal deltas : delta_array := (others => (others => '0'));
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

  process(clk)
    variable delta_v : signed(15 downto 0);
    variable sat5   : std_logic_vector(4 downto 0);
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
            end if;
            comp_data <= sample_in;
            comp_valid <= sample_valid;

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
              delta_v := signed(sample_pipe) - prev;
              if delta_v < -15 then sat5 := "10001";
              elsif delta_v > 15 then sat5 := "01111";
              else sat5 := std_logic_vector(delta_v(4 downto 0));
              end if;
              deltas(delta_cnt) <= sat5;
              prev <= signed(sample_pipe);

              if delta_v < -15 or delta_v > 15 then
                overflow_flag <= '1';
              end if;
              if sample_cnt = 15 then
                -- Block full: flush deltas. delta_cnt was NOT incremented for
                -- this sample (15th delta at index 14, delta_cnt still 14).
                -- Add one so FLUSH boundary checks include all 15 deltas.
                delta_cnt <= delta_cnt + 1;
                state <= FLUSH;
                out_idx <= 0;
              elsif overflow_flag = '1' then
                -- Delta overflow: emit verbatim-reset, then flush accumulated deltas
                -- First, emit the overflow reset word...
                comp_data <= '1' & sample_pipe(14 downto 0);  -- bit 15 = 1
                comp_valid <= '1';
                prev <= signed(sample_pipe);
                sample_cnt <= 0;
                delta_cnt <= 0;
                overflow_flag <= '0';
                sample_pipe_valid <= '0';
                -- Then flush accumulated deltas (delta_cnt entries)
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
            -- Pack deltas[out_idx*3], deltas[out_idx*3+1], deltas[out_idx*3+2]
            -- into one 16-bit word (if they exist).
            if out_idx * 3 < delta_cnt then
              comp_data(4 downto 0) <= deltas(out_idx * 3);
              if out_idx * 3 + 1 < delta_cnt then
                comp_data(9 downto 5) <= deltas(out_idx * 3 + 1);
              else
                comp_data(9 downto 5) <= (others => '0');
              end if;
              if out_idx * 3 + 2 < delta_cnt then
                comp_data(14 downto 10) <= deltas(out_idx * 3 + 2);
              else
                comp_data(14 downto 10) <= (others => '0');
              end if;
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
