library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity tb_fast_capture_budget is
end tb_fast_capture_budget;

architecture sim of tb_fast_capture_budget is
  constant MAX_COUNT : positive := 16;
  signal clk        : std_logic := '0';
  signal rst        : std_logic := '0';
  signal load       : std_logic := '0';
  signal load_count : natural range 1 to MAX_COUNT := 1;
  signal continuous : std_logic := '0';
  signal consume    : std_logic := '0';
  signal open_i     : std_logic;
  signal last_i     : std_logic;
  signal done_i     : std_logic;
  signal remaining_i : natural range 0 to MAX_COUNT;
begin
  clk <= not clk after 5 ns;

  dut : entity work.fast_capture_budget
    generic map (MAX_COUNT => MAX_COUNT)
    port map (
      clk => clk, rst => rst, load => load, load_count => load_count,
      continuous => continuous, consume => consume, budget_open => open_i,
      last => last_i, done => done_i, remaining => remaining_i);

  process
    procedure pulse_load(count : natural; mode : std_logic) is
    begin
      load_count <= count;
      continuous <= mode;
      load <= '1';
      wait until rising_edge(clk);
      load <= '0';
      wait for 1 ns;
      assert remaining_i = count report "load count mismatch" severity error;
    end procedure;

    procedure pulse_consume is
    begin
      consume <= '1';
      wait until rising_edge(clk);
      consume <= '0';
      wait for 1 ns;
    end procedure;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    rst <= '0';
    wait for 1 ns;
    assert open_i = '0' and done_i = '0' report "reset mismatch" severity error;

    pulse_load(3, '0');
    assert last_i = '0' report "unexpected last after load" severity error;
    pulse_consume;
    assert remaining_i = 2 and done_i = '0' severity error;
    pulse_consume;
    assert remaining_i = 1 and last_i = '1' severity error;
    pulse_consume;
    assert remaining_i = 0 and open_i = '0' and done_i = '1'
      report "single-shot final consume mismatch" severity error;
    wait until rising_edge(clk);
    wait for 1 ns;
    assert done_i = '0' report "done was not a pulse" severity error;

    pulse_load(2, '1');
    pulse_consume;
    assert remaining_i = 1 severity error;
    pulse_consume;
    assert remaining_i = 2 and done_i = '0'
      report "continuous reload mismatch" severity error;

    report "=== TB PASSED: fast_capture_budget edge cases ===" severity note;
    wait;
  end process;
end sim;
