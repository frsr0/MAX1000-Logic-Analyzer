library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Toggling-input regression for digital_rle: every completed run must report
-- its exact length, and ordinary transitions must never strobe overflow.
-- (A prev-based registered change detector re-fired one cycle after each
-- edge: every new run came out one sample short and overflow pulsed.)
entity tb_digital_rle_transitions is
end tb_digital_rle_transitions;

architecture bench of tb_digital_rle_transitions is
  constant CLK_PERIOD : time := 5 ns;
  constant HIGH_LEN   : natural := 30;
  constant LOW_LEN    : natural := 20;
  signal clk  : std_logic := '0';
  signal rst  : std_logic := '1';
  signal din  : std_logic_vector(15 downto 0) := (others => '0');
  signal po   : std_logic_vector(15 downto 0);
  signal pv   : std_logic;
  signal ov   : std_logic;
  signal runs_checked : natural := 0;
  signal done : boolean := false;
begin
  clk <= not clk after CLK_PERIOD / 2;

  dut : entity work.digital_rle
    port map (clk => clk, rst => rst, clk_en => '1', digital_in => din,
              packet_out => po, packet_valid => pv, packet_ready => '1',
              overflow => ov);

  check : process(clk)
    variable first : boolean := true;
    variable len   : natural;
  begin
    if rising_edge(clk) and rst = '0' then
      assert ov = '0' report "overflow strobed on an ordinary transition"
        severity failure;
      if pv = '1' and po(14 downto 13) = "00" and not done then
        len := to_integer(unsigned(po(8 downto 0))) + 1;
        if first then
          first := false;          -- leading run includes reset-release latency
        elsif po(9) = '1' then
          assert len = HIGH_LEN report "high run length " & integer'image(len)
            severity failure;
          runs_checked <= runs_checked + 1;
        else
          assert len = LOW_LEN report "low run length " & integer'image(len)
            severity failure;
          runs_checked <= runs_checked + 1;
        end if;
      end if;
    end if;
  end process;

  stim : process
  begin
    wait for 4 * CLK_PERIOD;
    wait until rising_edge(clk);
    rst <= '0';
    for k in 0 to 7 loop
      for i in 1 to LOW_LEN loop wait until rising_edge(clk); end loop;
      din(0) <= '1';
      for i in 1 to HIGH_LEN loop wait until rising_edge(clk); end loop;
      din(0) <= '0';
    end loop;
    for i in 1 to 40 loop wait until rising_edge(clk); end loop;
    done <= true;
    assert runs_checked >= 14
      report "too few runs checked: " & integer'image(runs_checked) severity failure;
    report "digital_rle transitions passed";
    std.env.finish;
    wait;
  end process;
end bench;
