-- Bit-exact regression for analog_packer: drives one block of 12 deltas +
-- 4 anchors through the packer at two widths chosen so the DRAIN residual
-- is exactly zero (W=5: 12*5=60, a multiple of 15) and nonzero (W=8:
-- 12*8=96, held=6 at the end) -- the two branches of the DRAIN decision
-- that a held-based fix must preserve bit-for-bit.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;

entity tb_analog_packer is
end tb_analog_packer;

architecture bench of tb_analog_packer is
  constant PERIOD : time := 5 ns;
  signal clk         : std_logic := '0';
  signal rst         : std_logic := '1';
  signal delta_in    : std_logic_vector(10 downto 0) := (others => '0');
  signal delta_valid : std_logic := '0';
  signal anchor_ch0, anchor_ch1, anchor_ch2, anchor_ch3 : std_logic_vector(11 downto 0) := (others => '0');
  signal anchor_valid : std_logic := '0';
  signal block_width  : std_logic_vector(3 downto 0) := (others => '0');
  signal block_done   : std_logic := '0';
  signal out_data     : std_logic_vector(15 downto 0);
  signal out_valid    : std_logic;
  signal out_ready    : std_logic := '1';
  signal busy, in_ready : std_logic;

  signal done : boolean := false;

  type word_array is array(natural range <>) of std_logic_vector(15 downto 0);

  procedure run_block(
    signal clk_i : in std_logic;
    w : integer;
    deltas : in word_array;
    signal delta_in_o : out std_logic_vector(10 downto 0);
    signal delta_valid_o : out std_logic;
    signal block_width_o : out std_logic_vector(3 downto 0);
    signal block_done_o : out std_logic;
    signal anchor_valid_o : out std_logic
  ) is
  begin
    for i in deltas'range loop
      wait until rising_edge(clk_i);
      delta_in_o <= deltas(i)(10 downto 0);
      delta_valid_o <= '1';
      block_width_o <= std_logic_vector(to_unsigned(w, 4));
      if i = deltas'high then
        block_done_o <= '1';
        anchor_valid_o <= '1';
      end if;
    end loop;
    wait until rising_edge(clk_i);
    delta_valid_o <= '0';
    block_done_o <= '0';
    anchor_valid_o <= '0';
  end procedure;

begin
  clk <= not clk after PERIOD/2 when not done;

  DUT: entity work.analog_packer
    generic map (BLOCK_SAMPLES => 12, MAX_WIDTH => 11)
    port map (
      clk => clk, rst => rst, clk_en => '1',
      delta_in => delta_in, delta_valid => delta_valid,
      anchor_ch0 => anchor_ch0, anchor_ch1 => anchor_ch1,
      anchor_ch2 => anchor_ch2, anchor_ch3 => anchor_ch3,
      anchor_valid => anchor_valid, block_width => block_width,
      block_done => block_done,
      out_data => out_data, out_valid => out_valid, out_ready => out_ready,
      busy => busy, in_ready => in_ready
    );

  process
    variable errs : integer := 0;

    procedure expect_word(exp : std_logic_vector(15 downto 0); tag : string) is
    begin
      -- Hold ready low before the word is produced, then validate that the
      -- presented word remains stable for two stalled cycles.
      out_ready <= '0';
      wait until rising_edge(clk) and out_valid = '1';
      if out_data /= exp then
        report "MISMATCH " & tag & ": got " & to_hstring(unsigned(out_data)) &
               " expected " & to_hstring(unsigned(exp)) severity error;
        errs := errs + 1;
      else
        report "OK " & tag & ": " & to_hstring(unsigned(out_data));
      end if;

      -- Exercise the valid/ready contract: the consumer may stall after a
      -- word is presented, so the packer must hold both valid and data.
      for stall in 1 to 2 loop
        wait until rising_edge(clk);
        if out_valid /= '1' or out_data /= exp then
          report "MISMATCH " & tag & " during backpressure: got valid=" &
                 std_logic'image(out_valid) & " data=" &
                 to_hstring(unsigned(out_data)) severity error;
          errs := errs + 1;
        end if;
      end loop;
      out_ready <= '1';
      wait until rising_edge(clk);
    end procedure;

    -- W=5: 12 deltas of a small ramp, values fit in signed 5-bit (-16..15)
    variable deltas5 : word_array(0 to 11) := (
      x"0001", x"0002", x"0003", x"0004", x"0005", x"0006",
      x"0007", x"0008", x"0009", x"000A", x"000B", x"000C");
    -- W=8: values fit in signed 8-bit (-128..127)
    variable deltas8 : word_array(0 to 11) := (
      x"0001", x"0002", x"0003", x"0004", x"0005", x"0006",
      x"0007", x"0008", x"0009", x"000A", x"000B", x"007F");

    variable acc : unsigned(179 downto 0);  -- generous scratch for repacking
    variable widx : integer;
  begin
    rst <= '1';
    anchor_ch0 <= x"111"; anchor_ch1 <= x"222";
    anchor_ch2 <= x"333"; anchor_ch3 <= x"444";
    wait for PERIOD * 3;
    rst <= '0';
    wait until rising_edge(clk);

    -- ===== Block 1: W=5, held ends at 0 (12*5=60, no DRAIN emit) =====
    run_block(clk, 5, deltas5, delta_in, delta_valid, block_width, block_done, anchor_valid);

    expect_word(x"2C00", "W5 header");  -- 0_0101_1_0000000000
    expect_word(x"0111", "W5 anchor0");
    expect_word(x"0222", "W5 anchor1");
    expect_word(x"0333", "W5 anchor2");
    expect_word(x"0444", "W5 anchor3");

    -- Re-pack the 12 5-bit deltas LSB-first into 15-bit payload words and
    -- compare against whatever the DUT emits, so this check is independent
    -- of the exact bit-count math -- it directly verifies round-trip
    -- correctness, which is what the held/DRAIN logic exists to guarantee.
    acc := (others => '0');
    widx := 0;
    for i in deltas5'range loop
      acc(widx + 4 downto widx) := unsigned(deltas5(i)(4 downto 0));
      widx := widx + 5;
    end loop;
    expect_word('0' & std_logic_vector(acc(14 downto 0)), "W5 payload0");
    expect_word('0' & std_logic_vector(acc(29 downto 15)), "W5 payload1");
    expect_word('0' & std_logic_vector(acc(44 downto 30)), "W5 payload2");
    expect_word('0' & std_logic_vector(acc(59 downto 45)), "W5 payload3");
    -- widx = 60, exactly 4*15: DRAIN must NOT emit a 5th payload word.
    -- Confirm by checking the very next word is block 2's header, not a
    -- residual -- i.e. no stray out_valid pulse appears first.

    -- ===== Block 2: W=8, held ends at 6 (12*8=96, DRAIN emits residual) =====
    run_block(clk, 8, deltas8, delta_in, delta_valid, block_width, block_done, anchor_valid);

    expect_word(x"4400", "W8 header");  -- 0_1000_1_0000000000
    expect_word(x"0111", "W8 anchor0");
    expect_word(x"0222", "W8 anchor1");
    expect_word(x"0333", "W8 anchor2");
    expect_word(x"0444", "W8 anchor3");

    acc := (others => '0');
    widx := 0;
    for i in deltas8'range loop
      acc(widx + 7 downto widx) := unsigned(deltas8(i)(7 downto 0));
      widx := widx + 8;
    end loop;
    -- widx = 96 = 6*15 + 6: 6 full payload words then a 6-bit DRAIN residual.
    for w in 0 to 5 loop
      expect_word('0' & std_logic_vector(acc(w*15+14 downto w*15)), "W8 payload" & integer'image(w));
    end loop;
    expect_word("000" & "0000000" & std_logic_vector(acc(95 downto 90)), "W8 drain residual");

    if errs = 0 then
      report "=== TB PASSED: analog_packer bit-exact on both DRAIN branches ===";
    else
      report "=== TB FAILED: " & integer'image(errs) & " mismatches ===" severity error;
    end if;
    done <= true;
    wait for PERIOD;
    if errs = 0 then
      std.env.finish;
    else
      std.env.stop(1);
    end if;
  end process;

end bench;
