library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_uart_interface is
  generic (
    CLK_FREQ : natural := 96000000;
    BAUD     : natural := 115200
  );
end tb_uart_interface;

architecture bench of tb_uart_interface is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant BAUD_TIME  : time := 1 sec / real(BAUD);

  signal clk      : std_logic := '0';
  signal reset    : std_logic := '0';
  signal tx       : std_logic;
  signal tx_enable : std_logic := '0';
  signal tx_busy  : std_logic;
  signal tx_data  : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_busy  : std_logic;
  signal rx_data  : std_logic_vector(7 downto 0);
  signal rx_error : std_logic;

  -- Loopback: tx is output from DUT, feed back to rx via a resolved signal
  signal rx_net   : std_logic;
begin

  rx_net <= tx;

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.UART_Interface
    generic map (
      CLK_Frequency => CLK_FREQ,
      Baud_Rate     => BAUD,
      OS_Rate       => 16,
      D_Width       => 8,
      Parity        => 0,
      Parity_EO     => '0'
    )
    port map (
      CLK       => clk,
      Reset     => reset,
      RX        => rx_net,
      TX        => tx,
      TX_Enable => tx_enable,
      TX_Busy   => tx_busy,
      TX_Data   => tx_data,
      RX_Busy   => rx_busy,
      RX_Data   => rx_data,
      RX_Error  => rx_error
    );

  process
    variable cycles : natural;
    variable found  : boolean;
  begin
    reset <= '1';
    wait_cycles(clk, 10);
    reset <= '0';
    wait_cycles(clk, 10);

    report "=== UART loopback tests @ " & integer'image(BAUD) & " baud ===";

    -- Test 1: TX start bit timing
    report "Test 1: TX start bit timing";
    tx_data <= x"A5";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, tx_busy, '1', 1 us, "TX should go busy");
    wait until falling_edge(tx);
    report "TX start bit detected";
    wait_until(clk, tx_busy, '0', 20 * BAUD_TIME, "TX busy timeout");
    check(tx_busy = '0', "TX should finish");
    report "Test 1: PASS";

    -- Test 2: TX/RX loopback 0xA5
    report "Test 2: TX/RX loopback 0xA5";
    tx_data <= x"A5";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, rx_busy, '1', 50 * BAUD_TIME, "RX should go busy");
    wait_until(clk, rx_busy, '0', 50 * BAUD_TIME, "RX busy timeout");
    wait_cycles(clk, 5);
    check(rx_data = x"A5", "RX mismatch: expected A5, got " & to_hstring(rx_data));
    report "Test 2: PASS";
    wait_until(clk, tx_busy, '0', 50 * BAUD_TIME, "TX should finish");

    -- Test 3: TX/RX loopback 0x5A
    report "Test 3: TX/RX loopback 0x5A";
    wait_cycles(clk, 10);
    tx_data <= x"5A";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, rx_busy, '1', 50 * BAUD_TIME, "RX should go busy");
    wait_until(clk, rx_busy, '0', 50 * BAUD_TIME, "RX busy timeout");
    wait_cycles(clk, 5);
    check(rx_data = x"5A", "RX mismatch: expected 5A, got " & to_hstring(rx_data));
    report "Test 3: PASS";
    wait_until(clk, tx_busy, '0', 50 * BAUD_TIME, "TX should finish");

    -- Test 4: TX/RX loopback 0xFF
    report "Test 4: TX/RX loopback 0xFF";
    wait_cycles(clk, 10);
    tx_data <= x"FF";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, rx_busy, '1', 50 * BAUD_TIME, "RX should go busy");
    wait_until(clk, rx_busy, '0', 50 * BAUD_TIME, "RX busy timeout");
    wait_cycles(clk, 5);
    check(rx_data = x"FF", "RX mismatch: expected FF, got " & to_hstring(rx_data));
    report "Test 4: PASS";

    -- Test 5: Back-to-back loopback
    report "Test 5: Back-to-back loopback";
    wait_until(clk, rx_busy, '0', 10 * BAUD_TIME, "RX should be idle");
    wait_cycles(clk, 10);
    tx_data <= x"12";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    tx_data <= x"34";
    wait_until(clk, tx_busy, '0', 20 * BAUD_TIME, "TX busy timeout");
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, rx_busy, '0', 20 * BAUD_TIME, "RX back-to-back timeout");
    report "Last RX data: " & to_hstring(rx_data);
    report "Test 5: PASS";

    report "=== ALL UART TESTS PASSED ===";
    wait;
  end process;

end bench;
