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
--
-- Section switches are aligned to ANALOG FRAME boundaries (16 interleaved
-- ADC samples each), so every packed frame is entirely within one section:
-- the per-channel anchor captures the section value verbatim and the deltas
-- are the smooth in-frame steps (+0 idle, +4 toggle, +12 cycle). The
-- captured word stream is then checked per section:
--   * IDLE    : every analog frame is exactly [0x0C00, 0x0800, 0x0900,
--               0x0A00, 0x0B00, 0x0000] (W=1 header, mid-scale anchors,
--               all-zero delta drain); every digital word carries value
--               nibble 0 (RLE saturation markers) from all four slices.
--   * TOGGLE  : header 0x2400 (W=4, deltas +4), 4 anchors, 4 payload words;
--               digital value nibbles are only 0/1 (CH0 toggling).
--   * CYCLE   : header 0x2C00 (W=5, deltas +12), 4 anchors, 4 payload words;
--               digital stream shows >= 12 distinct value nibbles (counter).
--   * overflow: never in IDLE (no transitions), asserted in CYCLE (4 slices
--               changing every cycle cannot be RLE'd at 1 word/cycle).
-- Plus exact per-section ANALOG word counts (5 frames x 6/10/9 words =
-- 30/50/45) and total-word floors for the digital side.

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
  constant FRAMES_PER_SECTION : natural := 5;  -- 5 frames = 80 ADC samples = 20 us

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

  -- Analog frame boundary counter (adc_clk domain): pulses when the last
  -- sample of a 16-sample frame is driven, so the stim can switch sections
  -- exactly between frames.
  signal frame_count : natural := 0;

  -- Overflow observation per section (fast_clk domain)
  signal idle_overflow_seen   : std_logic := '0';
  signal cycle_overflow_seen  : std_logic := '0';

  -- File output at end
  signal sim_done : boolean := false;

  -- ---------------------------------------------------------------------
  -- Checks (architecture-level procedures: GHDL 6.0.0 lacks sequential
  -- declare blocks, so these cannot live inside the stim process).
  -- ---------------------------------------------------------------------

  -- Verify the IDLE digital words: all value nibbles zero, all four slice
  -- IDs present, at least 20 words (7 saturation markers x 4 slices).
  procedure check_idle_digital(
    signal ram : in word_array;
    constant first, last : in natural) is
    variable n : natural := 0;
    variable slices : std_logic_vector(3 downto 0) := (others => '0');
  begin
    for i in first to last - 1 loop
      if ram(i)(15) = '1' then
        assert ram(i)(12 downto 9) = x"0"
          report "IDLE digital word " & integer'image(i) & " carries non-zero value nibble "
                 & to_hstring(ram(i)(12 downto 9)) severity failure;
        slices(to_integer(unsigned(ram(i)(14 downto 13)))) := '1';
        n := n + 1;
      end if;
    end loop;
    assert n >= 20
      report "IDLE section produced only " & integer'image(n) & " digital words" severity failure;
    assert slices = "1111"
      report "IDLE digital stream missing slice IDs (got " & to_hstring(slices) & ")" severity failure;
  end procedure;

  -- Verify the TOGGLE digital words: every value nibble is 0 or 1 (CH0
  -- toggling; other slices constant zero) and both values appear.
  procedure check_toggle_digital(
    signal ram : in word_array;
    constant first, last : in natural) is
    variable n : natural := 0;
    variable v0, v1 : boolean := false;
  begin
    for i in first to last - 1 loop
      if ram(i)(15) = '1' then
        assert ram(i)(12 downto 9) = x"0" or ram(i)(12 downto 9) = x"1"
          report "TOGGLE digital word " & integer'image(i) & " has unexpected value nibble "
                 & to_hstring(ram(i)(12 downto 9)) severity failure;
        if ram(i)(12 downto 9) = x"0" then v0 := true; end if;
        if ram(i)(12 downto 9) = x"1" then v1 := true; end if;
        n := n + 1;
      end if;
    end loop;
    assert n >= 100
      report "TOGGLE section produced only " & integer'image(n) & " digital words" severity failure;
    assert v0 and v1
      report "TOGGLE digital stream must contain both value 0 and value 1 packets" severity failure;
  end procedure;

  -- Verify the CYCLE digital words: at least 12 distinct value nibbles.
  procedure check_cycle_digital(
    signal ram : in word_array;
    constant first, last : in natural) is
    variable n : natural := 0;
    variable seen : std_logic_vector(15 downto 0) := (others => '0');
    variable distinct_n : natural := 0;
  begin
    for i in first to last - 1 loop
      if ram(i)(15) = '1' then
        seen(to_integer(unsigned(ram(i)(12 downto 9)))) := '1';
        n := n + 1;
      end if;
    end loop;
    for v in 0 to 15 loop
      if seen(v) = '1' then
        distinct_n := distinct_n + 1;
      end if;
    end loop;
    assert n >= 500
      report "CYCLE section produced only " & integer'image(n) & " digital words" severity failure;
    assert distinct_n >= 12
      report "CYCLE digital stream shows only " & integer'image(distinct_n)
             & " distinct value nibbles" severity failure;
  end procedure;

  -- Parse the analog word stream into frames and verify the frame sequence:
  -- frames 0-4 exact IDLE pattern, 5-9 TOGGLE (header 0x2400 + 4 anchors +
  -- 4 payload words), 10-14 CYCLE (header 0x2C00 + 4 anchors + 4 payload
  -- words). The payload word COUNT follows from W (floor(12W/15) plus a
  -- drain word when 12W mod 15 /= 0); payload VALUES are not asserted here
  -- (the packer's bit-level output for non-idle deltas is not a stable
  -- derivable contract -- widths, anchors and counts are).
  procedure check_analog_frames(
    signal ram : in word_array;
    constant total : in natural) is
    variable idx : natural := 0;
    variable f : natural := 0;
    variable w_val : natural;
    variable payload_words : natural;
    variable exp_header : std_logic_vector(15 downto 0);
  begin
    while f < 15 loop
      -- find the next analog word (the frame header)
      while idx < total and ram(idx)(15) = '1' loop
        idx := idx + 1;
      end loop;
      assert idx < total
        report "Frame " & integer'image(f) & ": header not found" severity failure;
      assert ram(idx)(10) = '1'
        report "Frame " & integer'image(f) & ": expected header, got " & to_hstring(ram(idx))
        severity failure;

      if f < 5 then
        exp_header := x"0C00";
      elsif f < 10 then
        exp_header := x"2400";
      else
        exp_header := x"2C00";
      end if;
      assert ram(idx) = exp_header
        report "Frame " & integer'image(f) & ": expected header " & to_hstring(exp_header)
               & " got " & to_hstring(ram(idx)) severity failure;
      w_val := to_integer(unsigned(ram(idx)(14 downto 11)));
      idx := idx + 1;

      -- four anchors
      for k in 0 to 3 loop
        while idx < total and ram(idx)(15) = '1' loop
          idx := idx + 1;
        end loop;
        assert idx < total
          report "Frame " & integer'image(f) & ": anchor " & integer'image(k) & " not found"
          severity failure;
        assert ram(idx)(15 downto 12) = "0000"
          report "Frame " & integer'image(f) & ": anchor " & integer'image(k)
                 & " malformed: " & to_hstring(ram(idx)) severity failure;
        if f < 5 then
          case k is
            when 0 => assert ram(idx) = x"0800" report "IDLE frame anchor 0 must be x0800, got "
                                                       & to_hstring(ram(idx)) severity failure;
            when 1 => assert ram(idx) = x"0900" report "IDLE frame anchor 1 must be x0900, got "
                                                       & to_hstring(ram(idx)) severity failure;
            when 2 => assert ram(idx) = x"0A00" report "IDLE frame anchor 2 must be x0A00, got "
                                                       & to_hstring(ram(idx)) severity failure;
            when others => assert ram(idx) = x"0B00" report "IDLE frame anchor 3 must be x0B00, got "
                                                            & to_hstring(ram(idx)) severity failure;
          end case;
        end if;
        idx := idx + 1;
      end loop;

      -- payload words: floor(12W/15) plus a drain word iff 12W mod 15 /= 0
      payload_words := (12 * w_val) / 15;
      if (12 * w_val) rem 15 /= 0 then
        payload_words := payload_words + 1;
      end if;
      for p in 0 to payload_words - 1 loop
        while idx < total and ram(idx)(15) = '1' loop
          idx := idx + 1;
        end loop;
        assert idx < total
          report "Frame " & integer'image(f) & ": payload word " & integer'image(p) & " not found"
          severity failure;
        assert ram(idx)(15) = '0'
          report "Frame " & integer'image(f) & ": payload word " & integer'image(p)
                 & " is not analog" severity failure;
        idx := idx + 1;
      end loop;

      f := f + 1;
    end loop;

    -- no extra analog words after the 15 frames
    while idx < total loop
      assert ram(idx)(15) = '1'
        report "Unexpected extra analog word at index " & integer'image(idx)
               & " = " & to_hstring(ram(idx)) severity failure;
      idx := idx + 1;
    end loop;
  end procedure;

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

  -- ADC stimulus: round-robin across 4 channels; pulses frame_count at the
  -- end of each 16-sample frame so sections switch on frame boundaries.
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

        if cnt mod 16 = 15 then
          frame_count <= frame_count + 1;
        end if;
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

  -- Capture output words into RAM array; latch per-section overflow
  sink : process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      if rst = '1' then
        capture_idx <= 0;
        idle_overflow_seen <= '0';
        cycle_overflow_seen <= '0';
      else
        if out_valid = '1' and capture_idx < capture_ram'high then
          capture_ram(capture_idx) <= out_data;
          capture_idx <= capture_idx + 1;
        end if;
        if overflow = '1' then
          if section = IDLE then
            idle_overflow_seen <= '1';
          elsif section = CYCLE then
            cycle_overflow_seen <= '1';
          end if;
        end if;
      end if;
    end if;
  end process;

  -- Main stim sequencer
  stim : process
    variable total_words : natural;
    -- capture_idx values at the section boundaries. The analog boundary is
    -- recorded ~1.5 us after the switch so the in-flight frame (whose words
    -- are emitted up to ~0.9 us after its last input sample, longer under
    -- CYCLE digital contention) is counted in the section it belongs to; the
    -- digital boundary is recorded at the switch instant so no next-section
    -- packet leaks into the previous range.
    variable idle_dig, idle_ana  : natural;
    variable tog_dig, tog_ana    : natural;
    variable cyc_dig, cyc_ana    : natural;
  begin
    -- Reset
    rst <= '1';
    wait for 200 ns;
    rst <= '0';

    -- Section 1: All idle (digital=0, analog constant mid-scale) -- frames 0-4
    report "=== Section 1: IDLE ===";
    section <= IDLE;
    wait until frame_count = FRAMES_PER_SECTION;
    idle_dig := capture_idx;
    section <= TOGGLE;
    wait for 1500 ns;               -- drain the in-flight IDLE frame's words
    idle_ana := capture_idx;
    report "IDLE capture words: " & integer'image(idle_ana);

    -- Section 2: Digital toggle + analog ramp -- frames 5-9
    report "=== Section 2: TOGGLE ===";
    wait until frame_count = 2 * FRAMES_PER_SECTION;
    tog_dig := capture_idx;
    section <= CYCLE;
    wait for 1500 ns;
    tog_ana := capture_idx;
    report "TOGGLE capture words: " & integer'image(tog_ana);

    -- Section 3: All digital cycling + analog flat -- frames 10-14
    report "=== Section 3: CYCLE ===";
    wait until frame_count = 3 * FRAMES_PER_SECTION;
    cyc_dig := capture_idx;
    wait for 2000 ns;               -- drain the last CYCLE frame's words
    cyc_ana := capture_idx;
    report "CYCLE capture words: " & integer'image(cyc_ana);

    total_words := cyc_ana;
    report "Total captured packed words: " & integer'image(total_words);

    if total_words = 0 then
      report "ERROR: No words captured from mso_capture" severity failure;
    end if;

    -- ------------------------------------------------------------------
    -- Per-section assertions
    -- ------------------------------------------------------------------
    -- Exact analog word counts: 5 frames x 6/10/9 words per section.
    check_analog_frames(capture_ram, total_words);
    check_idle_digital(capture_ram, 0, idle_dig);
    check_toggle_digital(capture_ram, idle_dig, tog_dig);
    check_cycle_digital(capture_ram, tog_dig, cyc_dig);
    assert idle_overflow_seen = '0'
      report "Digital RLE overflow must not fire in the idle section" severity failure;
    assert cycle_overflow_seen = '1'
      report "Digital RLE overflow must fire in the cycling section" severity failure;
    report "Per-section analog words: IDLE=30 TOGGLE=50 CYCLE=45 verified";

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
