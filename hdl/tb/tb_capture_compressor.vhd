library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_capture_compressor is
end tb_capture_compressor;

architecture bench of tb_capture_compressor is
  constant CLK_PERIOD : time := 6 ns;  -- 166.7 MHz

  signal clk          : std_logic := '0';
  signal rst          : std_logic := '0';
  signal sample_in    : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_valid : std_logic := '0';
  signal comp_enable  : std_logic := '0';
  signal comp_data    : std_logic_vector(15 downto 0);
  signal comp_valid   : std_logic;
  signal comp_busy    : std_logic;

  type word_array is array(natural range <>) of std_logic_vector(15 downto 0);
  type word6 is array(0 to 5) of std_logic_vector(15 downto 0);

  -- Feed 16 samples and capture up to 6 output words
  procedure feed_and_capture(
    signal   clk          : in    std_logic;
    signal   sample_in    : out   std_logic_vector(15 downto 0);
    signal   sample_valid : out   std_logic;
    constant samples      : in    word_array;
    variable n_out        : out   natural;
    variable out_words    : out   word6
  ) is
  begin
    n_out := 0;
    out_words := (others => (others => '0'));

    for i in samples'range loop
      sample_in <= samples(i);
      sample_valid <= '1';
      wait until rising_edge(clk);
      wait for 0 ns;
      if comp_valid = '1' then
        out_words(n_out) := comp_data;
        n_out := n_out + 1;
      end if;
    end loop;
    sample_valid <= '0';

    for i in 0 to 20 loop
      wait until rising_edge(clk);
      wait for 0 ns;
      exit when comp_valid = '0' and n_out > 0 and i > 5;
      if comp_valid = '1' then
        out_words(n_out) := comp_data;
        n_out := n_out + 1;
        exit when n_out >= 6;
      end if;
    end loop;
  end procedure;

  procedure check_block(
    constant test_name : in  string;
    constant got       : in  word6;
    constant n_got     : in  natural;
    constant expected  : in  word6;
    constant n_exp     : in  natural
  ) is
  begin
    check(n_got = n_exp, test_name & ": word count: got " &
      integer'image(n_got) & " expected " & integer'image(n_exp));
    for i in 0 to n_exp - 1 loop
      check(got(i) = expected(i), test_name & ": word " &
        integer'image(i) & ": got 0x" & to_hstring(got(i)) &
        " expected 0x" & to_hstring(expected(i)));
    end loop;
    report test_name & " PASSED (" & integer'image(n_got) & " words)";
  end procedure;

  -- Overflow test as procedure (avoids VHDL-2008 declare-block issues)
  procedure run_overflow(
    signal   clk          : in    std_logic;
    signal   sample_in    : out   std_logic_vector(15 downto 0);
    signal   sample_valid : out   std_logic;
    signal   comp_data    : in    std_logic_vector(15 downto 0);
    signal   comp_valid   : in    std_logic
  ) is
    variable ov_samples : word_array(0 to 4) := (x"0000", x"0100", x"0000", x"0000", x"0000");
    variable n_ov       : natural := 0;
    variable ov_out     : word6;
    variable found_vr   : boolean := false;
  begin
    for i in 0 to 4 loop
      sample_in <= ov_samples(i);
      sample_valid <= '1';
      wait until rising_edge(clk);
      wait for 0 ns;
      if comp_valid = '1' then
        ov_out(n_ov) := comp_data;
        report "  overflow output[" & integer'image(n_ov) & "] = 0x" &
          to_hstring(comp_data) & " (bit15=" & std_logic'image(comp_data(15)) & ")";
        n_ov := n_ov + 1;
      end if;
    end loop;
    sample_valid <= '0';

    for i in 0 to 10 loop
      wait until rising_edge(clk);
      wait for 0 ns;
      exit when comp_valid = '0' and n_ov > 1;
      if comp_valid = '1' then
        ov_out(n_ov) := comp_data;
        report "  overflow output[" & integer'image(n_ov) & "] = 0x" &
          to_hstring(comp_data);
        n_ov := n_ov + 1;
      end if;
    end loop;

    report "Overflow: " & integer'image(n_ov) & " words emitted";
    check(n_ov >= 2, "Overflow: fewer than 2 words emitted");

    found_vr := false;
    for i in 0 to n_ov - 1 loop
      if ov_out(i)(15) = '1' then
        found_vr := true;
        report "  verbatim-reset found at word " & integer'image(i);
      end if;
    end loop;
    check(found_vr, "Overflow: no verbatim-reset word (bit15=1) found");
    report "Overflow test PASSED";
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT: entity work.capture_compressor
    port map (
      clk               => clk,
      rst               => rst,
      sample_in         => sample_in,
      sample_valid      => sample_valid,
      compression_enable => comp_enable,
      comp_data          => comp_data,
      comp_valid         => comp_valid,
      busy               => comp_busy
    );

  process
    variable n_got : natural;
    variable out_w : word6;

    -- Pre-built sample vectors
    variable dc_samples  : word_array(0 to 15);
    variable ramp_up     : word_array(0 to 15);
    variable ramp_down   : word_array(0 to 15);
    variable alt_samples : word_array(0 to 15);

  begin
    -- Build test vectors
    for i in 0 to 15 loop
      dc_samples(i)  := x"0000";
      ramp_up(i)     := std_logic_vector(to_unsigned(i, 16));
      ramp_down(i)   := std_logic_vector(to_unsigned(16 - i, 16));
      alt_samples(i) := x"0000";
    end loop;
    for i in 1 to 15 loop
      if i mod 2 = 1 then alt_samples(i) := x"0002"; end if;
    end loop;

    -------------------------------------------------------------------
    -- Reset
    -------------------------------------------------------------------
    rst <= '1';
    wait_cycles(clk, 4);
    rst <= '0';
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    -- 1. Passthrough mode
    -------------------------------------------------------------------
    report "=== 1. Passthrough test ===";
    comp_enable <= '0';
    wait_cycles(clk, 4);

    sample_in <= x"AAAA";
    sample_valid <= '1';
    wait_cycles(clk, 1);
    sample_valid <= '0';
    wait for 0 ns;
    check(comp_valid = '1', "PASSTHROUGH: comp_valid not asserted");
    check(comp_data = x"AAAA", "PASSTHROUGH: data 0x" & to_hstring(comp_data) & " /= 0xAAAA");
    wait_cycles(clk, 2);

    sample_in <= x"5555";
    sample_valid <= '1';
    wait_cycles(clk, 1);
    sample_valid <= '0';
    wait for 0 ns;
    check(comp_valid = '1', "PASSTHROUGH: comp_valid not asserted (2)");
    check(comp_data = x"5555", "PASSTHROUGH: data 0x" & to_hstring(comp_data) & " /= 0x5555");
    wait_cycles(clk, 4);
    report "Passthrough PASSED";

    -------------------------------------------------------------------
    -- 2. DC (all zeros)
    -------------------------------------------------------------------
    report "=== 2. DC (all zeros) ===";
    comp_enable <= '1';
    wait_cycles(clk, 4);

    feed_and_capture(clk, sample_in, sample_valid, dc_samples, n_got, out_w);
    check_block("DC", out_w, n_got,
      (x"0000", x"0000", x"0000", x"0000", x"0000", x"0000"), 6);
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    -- 3. Ramp +1 (0, 1, 2, ..., 15)
    -------------------------------------------------------------------
    report "=== 3. Ramp +1 ===";
    feed_and_capture(clk, sample_in, sample_valid, ramp_up, n_got, out_w);
    check_block("RAMP+1", out_w, n_got,
      (x"0000", x"0421", x"0421", x"0421", x"0421", x"0421"), 6);
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    -- 4. Ramp -1 (0x0010, 0x000F, ..., 0x0001)
    -------------------------------------------------------------------
    report "=== 4. Ramp -1 ===";
    feed_and_capture(clk, sample_in, sample_valid, ramp_down, n_got, out_w);
    check_block("RAMP-1", out_w, n_got,
      (x"0010", x"7FFF", x"7FFF", x"7FFF", x"7FFF", x"7FFF"), 6);
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    -- 5. Alternating 0x0000, 0x0002 (deltas ±2)
    -------------------------------------------------------------------
    report "=== 5. Alternating ±2 ===";
    feed_and_capture(clk, sample_in, sample_valid, alt_samples, n_got, out_w);
    check_block("ALT", out_w, n_got,
      (x"0000", x"0BC2", x"785E", x"0BC2", x"785E", x"0BC2"), 6);
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    report "=== 6. Overflow test ===";
    run_overflow(clk, sample_in, sample_valid, comp_data, comp_valid);
    wait_cycles(clk, 4);

    -------------------------------------------------------------------
    -- Summary
    -------------------------------------------------------------------
    report "=== ALL CAPTURE COMPRESSOR TESTS PASSED ===";
    assert false report "SIMULATION COMPLETE" severity failure;
    wait;
  end process;

end bench;
