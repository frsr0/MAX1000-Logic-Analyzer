library ieee;
use ieee.std_logic_1164.all;

entity tb_bit_engine_repeat is
end entity;

architecture bench of tb_bit_engine_repeat is
  signal clk         : std_logic := '0';
  signal tx_data     : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_we       : std_logic := '0';
  signal tx_used     : std_logic_vector(7 downto 0);
  signal rx_data     : std_logic_vector(7 downto 0);
  signal rx_re       : std_logic := '0';
  signal rx_used     : std_logic_vector(7 downto 0);
  signal rx_overflow : std_logic;
  signal busy        : std_logic;
  signal done        : std_logic;
  signal start       : std_logic := '0';
  signal clear       : std_logic := '0';
  signal out_0       : std_logic;
  signal out_1       : std_logic;
begin
  clk <= not clk after 5 ns;

  dut : entity work.Bit_Engine
    port map (
      CLK         => clk,
      TX_Data     => tx_data,
      TX_We       => tx_we,
      TX_Used     => tx_used,
      RX_Data     => rx_data,
      RX_Re       => rx_re,
      RX_Used     => rx_used,
      RX_Overflow => rx_overflow,
      Bit_Div     => x"000000",
      Num_Syms    => x"FFFF",
      Over_Sample => "00",
      RX_Enable   => '0',
      Clk_Toggle  => '0',
      Start       => start,
      Repeat      => '1',
      Busy        => busy,
      Done        => done,
      Clear       => clear,
      Out_0       => out_0,
      Out_1       => out_1,
      In_0        => '1'
    );

  process
    variable saw_busy : boolean := false;
  begin
    tx_data <= x"01";
    tx_we <= '1';
    wait until rising_edge(clk);
    tx_we <= '0';
    start <= '1';
    wait until rising_edge(clk);
    start <= '0';

    for i in 0 to 80 loop
      wait until rising_edge(clk);
      saw_busy := saw_busy or (busy = '1');
      assert done = '0' report "repeat mode asserted Done" severity failure;
    end loop;
    assert saw_busy report "repeat mode never became busy" severity failure;

    clear <= '1';
    wait until rising_edge(clk);
    clear <= '0';
    wait until rising_edge(clk);
    assert busy = '0' report "Clear did not stop repeat mode" severity failure;
    report "PASS: Bit_Engine repeats a loaded pattern until Clear" severity note;
    wait;
  end process;
end architecture;
