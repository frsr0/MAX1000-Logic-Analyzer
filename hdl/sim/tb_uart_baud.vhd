library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_uart_baud is
  generic (
    BAUD     : integer := 921600;  -- baud rate to test
    CLK_FREQ : integer := 48000000
  );
end tb_uart_baud;

architecture sim of tb_uart_baud is
  constant CLK_PERIOD : time := 20.833 ns;  -- 48 MHz
  constant RST_BITS   : integer := 10;      -- 1 start + 8 data + 1 stop

  signal clk       : std_logic := '0';
  signal running   : boolean := true;

  -- UART signals
  signal reset     : std_logic := '0';
  signal uart_rx   : std_logic := '1';
  signal uart_tx   : std_logic;
  signal tx_enable : std_logic := '0';
  signal tx_busy   : std_logic;
  signal tx_data   : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_busy   : std_logic;
  signal rx_data   : std_logic_vector(7 downto 0);
  signal rx_error  : std_logic;

  -- Expected TX bit time for this baud rate
  function bit_time_ns(baud : integer) return time is
  begin
    return (1 sec) / baud;
  end function;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.UART_Interface(behavioral)
    generic map (
      CLK_Frequency => CLK_FREQ,
      Baud_Rate     => BAUD,
      OS_Rate       => 13,
      D_Width       => 8,
      Parity        => 0
    )
    port map (
      CLK => clk,
      Reset => reset,
      RX => uart_tx,     -- loopback: TX wired to RX
      TX => uart_tx,
      TX_Enable => tx_enable,
      TX_Busy => tx_busy,
      TX_Data => tx_data,
      RX_Busy => rx_busy,
      RX_Data => rx_data,
      RX_Error => rx_error
    );

  process
    variable rx_byte    : std_logic_vector(7 downto 0);
    variable rx_timeout : integer := 0;
    variable bit_t      : time := bit_time_ns(BAUD);
    variable test_data  : std_logic_vector(7 downto 0);
  begin
    wait for 10 us;
    reset <= '1';
    wait for 1 us;
    reset <= '0';
    wait for 10 us;

    report "=== Baud rate: " & integer'image(BAUD) &
           " (" & integer'image(BAUD / 1000000) & "." &
           integer'image((BAUD / 100000) mod 10) & " Mbps) ===" severity note;
    report "  Bit time: " & integer'image(1000000000 / BAUD) & " ns" severity note;

    -- Test 3 different byte values
    for byte_idx in 0 to 2 loop
      case byte_idx is
        when 0 => test_data := x"A5";
        when 1 => test_data := x"5A";
        when 2 => test_data := x"FF";
      end case;

      -- Transmit
      tx_data <= test_data;
      tx_enable <= '1';
      wait for CLK_PERIOD * 2;
      tx_enable <= '0';

      -- Wait for TX to finish (10 bits = 1 start + 8 data + 1 stop)
      wait for bit_t * RST_BITS * 2;
      -- Additional wait for any pipeline
      wait for bit_t * 2;

      -- Check RX
      if rx_busy = '1' then
        wait until rx_busy = '0' for bit_t * 10;
      end if;

      wait for CLK_PERIOD;

      if rx_data = test_data then
        report "  Byte " & integer'image(byte_idx) &
               ": sent=0x" & integer'image(to_integer(unsigned(test_data))) &
               " rx=0x" & integer'image(to_integer(unsigned(rx_data))) & " OK" severity note;
      elsif rx_error = '1' then
        report "  Byte " & integer'image(byte_idx) &
               ": sent=0x" & integer'image(to_integer(unsigned(test_data))) &
               " RX_ERROR" severity failure;
      else
        report "  Byte " & integer'image(byte_idx) &
               ": sent=0x" & integer'image(to_integer(unsigned(test_data))) &
               " rx=0x" & integer'image(to_integer(unsigned(rx_data))) &
               " MISMATCH" severity failure;
      end if;

      wait for bit_t * 3;  -- gap between bytes
    end loop;

    report "=== PASS at " & integer'image(BAUD) & " baud ===" severity note;
    running <= false;
    wait;
  end process;

end sim;
