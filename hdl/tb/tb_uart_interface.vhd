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

  -- Loopback: tx is output from DUT, feed back to rx via a resolved signal.
  -- Test 6 disconnects the loopback and drives RX directly with a broken
  -- (short-stop-bit) frame to exercise RX_Error.
  signal loopback_en : std_logic := '1';
  signal manual_rx   : std_logic := '1';
  signal rx_net   : std_logic;
begin

  rx_net <= tx when loopback_en = '1' else manual_rx;

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
    wait_until(clk, tx_busy, '0', 20 * BAUD_TIME, "TX should be idle");
    wait_cycles(clk, 10);
    -- first byte 0x12
    tx_data <= x"12";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    wait_until(clk, tx_busy, '1', 20 * BAUD_TIME, "TX1 should go busy");
    wait_until(clk, tx_busy, '0', 20 * BAUD_TIME, "TX1 should finish");
    -- second byte 0x34 queued while TX1 was still running, transmitted
    -- immediately after, so the two TX frames are back-to-back
    tx_data <= x"34";
    tx_enable <= '1';
    wait_cycles(clk, 1);
    tx_enable <= '0';
    -- RX1's frame ends within ~0.5 bit of TX1's end (os-tick phase); wait for
    -- its completion (no-op if it already finished), then verify its data.
    -- The previous code waited for rx_busy='0' while it was already low
    -- between the frames and so reported the stale byte (x"FF" from Test 4).
    wait_until(clk, rx_busy, '0', 20 * BAUD_TIME, "RX1 should finish");
    wait_cycles(clk, 2);
    check(rx_data = x"12", "RX first byte mismatch: expected 12, got " & to_hstring(rx_data));
    -- now wait for the SECOND RX frame and verify its data
    wait_until(clk, rx_busy, '1', 20 * BAUD_TIME, "RX second frame should start");
    wait_until(clk, rx_busy, '0', 20 * BAUD_TIME, "RX second frame should finish");
    wait_cycles(clk, 5);
    check(rx_data = x"34", "RX back-to-back second byte mismatch: expected 34, got " & to_hstring(rx_data));
    wait_until(clk, tx_busy, '0', 20 * BAUD_TIME, "TX2 should finish");
    report "Test 5: PASS";

    -- Test 6: RX framing error (stop bit held low) -> RX_Error pulse
    report "Test 6: RX framing error (short stop bit)";
    loopback_en <= '0';
    manual_rx <= '1';
    wait_cycles(clk, 10);
    -- One 8N1 frame of 0x00 with the STOP bit driven low: the receiver's
    -- mid-stop sample sees '0', so RX_Error must assert when the frame ends.
    -- (The trailing low also re-arms the receiver for a follow-up frame, so
    -- the error is asserted from its latch rather than from the busy cycle.)
    manual_rx <= '0';                  -- start bit
    wait for BAUD_TIME;
    for i in 0 to 7 loop
      manual_rx <= '0';                -- data bits (0x00)
      wait for BAUD_TIME;
    end loop;
    manual_rx <= '0';                  -- broken stop bit
    wait for BAUD_TIME;
    manual_rx <= '1';                  -- line idle again
    wait_until(clk, rx_error, '1', 20 * BAUD_TIME, "RX_Error must assert on a short stop bit");
    wait_cycles(clk, 5);
    check(rx_data = x"00", "RX data bits must still be sampled (expected 00, got " & to_hstring(rx_data) & ")");
    loopback_en <= '1';
    report "Test 6: PASS";

    report "=== ALL UART TESTS PASSED ===";
    wait;
  end process;

end bench;
