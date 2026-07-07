-- End-to-end test: mso_capture produces packed words, then Python decoder
-- reconstructs the original stimulus.
--
-- Drives known digital and ADC patterns, captures all output words into a RAM
-- array, then dumps them to "mso_capture_words.txt". A post-sim Python script
-- reads the file, decodes via packed_decoder.decode(), and verifies against
-- the expected stimulus.
--
-- Three stimulus sections:
--   1. All channels idle (digital=0, analog=const)
--   2. Single digital channel toggling + analog ramp
--   3. All digital channels cycling (0..65535) + analog flat

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.textio.all;

entity tb_mso_full_roundtrip is
end tb_mso_full_roundtrip;

architecture sim of tb_mso_full_roundtrip is
  constant FAST_PERIOD : time := 5 ns;   -- 200 MHz
  constant ADC_PERIOD  : time := 250 ns;  -- 4 MHz ADC scan rate (realistic)
  constant STIM_SAMPLES : natural := 128; -- digital samples to generate per section

  signal fast_clk : std_logic := '0';
  signal adc_clk  : std_logic := '0';
  signal rst      : std_logic := '1';

  -- ADC stimulus
  signal adc_ch0, adc_ch1, adc_ch2, adc_ch3 : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_ch0_valid, adc_ch1_valid, adc_ch2_valid, adc_ch3_valid : std_logic := '0';

  -- Digital stimulus
  signal digital_in : std_logic_vector(15 downto 0) := (others => '0');

  -- mso_capture output
  signal out_data   : std_logic_vector(15 downto 0);
  signal out_valid  : std_logic;
  signal out_ready  : std_logic := '1';
  signal overflow   : std_logic;

  -- Word capture
  type word_array is array(0 to 65535) of std_logic_vector(15 downto 0);
  signal capture_ram : word_array := (others => (others => '0'));
  signal capture_idx : natural := 0;

  -- Stimulus section control
  type section_t is (IDLE, TOGGLE, CYCLE, DONE);
  signal section : section_t := IDLE;
  signal adc_phase : natural := 0;

  -- File output at end
  signal sim_done : boolean := false;

begin
  fast_clk <= not fast_clk after FAST_PERIOD / 2;
  adc_clk  <= not adc_clk  after ADC_PERIOD / 2;

  dut : entity work.mso_capture
    port map (
      fast_clk      => fast_clk,
      adc_clk       => adc_clk,
      rst           => rst,
      adc_ch0       => adc_ch0,
      adc_ch0_valid => adc_ch0_valid,
      adc_ch1       => adc_ch1,
      adc_ch1_valid => adc_ch1_valid,
      adc_ch2       => adc_ch2,
      adc_ch2_valid => adc_ch2_valid,
      adc_ch3       => adc_ch3,
      adc_ch3_valid => adc_ch3_valid,
      digital_in    => digital_in,
      out_data      => out_data,
      out_valid     => out_valid,
      out_ready     => out_ready,
      dig_overflow  => overflow
    );

  -- ADC stimulus: round-robin across 4 channels
  adc_stim : process(adc_clk)
    variable cnt : natural := 0;
    variable base : natural;
  begin
    if rising_edge(adc_clk) then
      adc_ch0_valid <= '0';
      adc_ch1_valid <= '0';
      adc_ch2_valid <= '0';
      adc_ch3_valid <= '0';

      if rst = '0' then
        case section is
          when IDLE =>
            base := 16#800#;  -- mid-scale
          when TOGGLE =>
            base := 16#800# + cnt;
          when others =>
            base := 16#400# + (cnt * 3) mod 2048;
        end case;

        case cnt mod 4 is
          when 0 =>
            adc_ch0 <= std_logic_vector(to_unsigned(base, 12));
            adc_ch0_valid <= '1';
          when 1 =>
            adc_ch1 <= std_logic_vector(to_unsigned(base + 16#100#, 12));
            adc_ch1_valid <= '1';
          when 2 =>
            adc_ch2 <= std_logic_vector(to_unsigned(base + 16#200#, 12));
            adc_ch2_valid <= '1';
          when others =>
            adc_ch3 <= std_logic_vector(to_unsigned(base + 16#300#, 12));
            adc_ch3_valid <= '1';
        end case;
        cnt := cnt + 1;
      end if;
    end if;
  end process;

  -- Digital stimulus generator
  digital_stim : process(fast_clk)
    variable tick : natural := 0;
    variable ch0 : std_logic := '0';
  begin
    if rising_edge(fast_clk) then
      case section is
        when IDLE =>
          digital_in <= (others => '0');
        when TOGGLE =>
          -- Channel 0 toggles every 4 fast_clk cycles
          if tick mod 4 = 0 then
            ch0 := not ch0;
          end if;
          digital_in(0) <= ch0;
          digital_in(15 downto 1) <= (others => '0');
          tick := tick + 1;
        when CYCLE =>
          digital_in <= std_logic_vector(unsigned(digital_in) + 1);
        when others =>
          null;
      end case;
    end if;
  end process;

  -- Capture output words into RAM array
  sink : process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      if out_valid = '1' and capture_idx < capture_ram'high then
        capture_ram(capture_idx) <= out_data;
        capture_idx <= capture_idx + 1;
      end if;
    end if;
  end process;

  -- Main stim sequencer
  stim : process
    variable adc_blocks : natural;
    variable total_words : natural;
  begin
    -- Reset
    rst <= '1';
    wait for 200 ns;
    rst <= '0';
    wait for 1 us;

    -- Section 1: All idle (digital=0, analog constant mid-scale)
    -- Run for enough ADC cycles to complete ~4 analog blocks
    report "=== Section 1: IDLE ===";
    section <= IDLE;
    wait for 20 us;
    report "Capture words so far: " & integer'image(capture_idx);

    -- Section 2: Digital toggle + analog ramp
    report "=== Section 2: TOGGLE ===";
    section <= TOGGLE;
    wait for 20 us;
    report "Capture words so far: " & integer'image(capture_idx);

    -- Section 3: All digital cycling + analog flat
    report "=== Section 3: CYCLE ===";
    section <= CYCLE;
    wait for 20 us;
    report "Capture words so far: " & integer'image(capture_idx);

    total_words := capture_idx;
    report "Total captured packed words: " & integer'image(total_words);

    if total_words = 0 then
      report "ERROR: No words captured from mso_capture" severity failure;
    end if;

    -- Write captured words to file
    report "Writing mso_capture_words.txt...";
    sim_done <= true;
    wait for 10 ns;
    std.env.finish;
    wait;
  end process;

  -- File writer (driven by sim_done)
  file_writer : process
    file f : text open write_mode is "mso_capture_words.txt";
    variable l : line;
    variable w : natural;
  begin
    wait until sim_done;
    for i in 0 to capture_idx - 1 loop
      w := to_integer(unsigned(capture_ram(i)));
      write(l, w);
      writeline(f, l);
    end loop;
    file_close(f);
    report "mso_capture_words.txt written with " & integer'image(capture_idx) & " words";
    wait;
  end process;

end sim;
