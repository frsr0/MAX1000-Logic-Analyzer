library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_multi_byte_cmd is
  generic (TEST : string := "tc_divider");
end tb_multi_byte_cmd;

architecture sim of tb_multi_byte_cmd is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant BIT_TIME   : time := 26 * CLK_PERIOD;  -- ~923 kbps @ 24 MHz

  signal clk       : std_logic := '0';
  signal running   : boolean := true;

  signal uart_rx   : std_logic := '1';
  signal uart_tx   : std_logic;

  -- SPI-iface signals (shared with UART_RX pin for SCK, UART_TX pin for MOSI)
  signal spi_sck   : std_logic := '0';
  signal spi_mosi  : std_logic := '0';
  signal spi_miso  : std_logic;
  signal spi_cs    : std_logic := '1';
  signal int_mode  : std_logic;

  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  procedure uart_send_byte(signal rx : out std_logic; data : std_logic_vector(7 downto 0)) is
  begin
    rx <= '0'; wait for BIT_TIME;  -- start bit
    for i in 0 to 7 loop
      rx <= data(i); wait for BIT_TIME;
    end loop;
    rx <= '1'; wait for BIT_TIME;  -- stop bit
  end;

  procedure uart_send_le32(signal rx : out std_logic; val : natural) is
    variable v : std_logic_vector(31 downto 0) := std_logic_vector(to_unsigned(val, 32));
  begin
    uart_send_byte(rx, v(7 downto 0));
    uart_send_byte(rx, v(15 downto 8));
    uart_send_byte(rx, v(23 downto 16));
    uart_send_byte(rx, v(31 downto 24));
  end;

  -- SPI xfer: send 5 bytes (cmd + 4 data), read 5 MISO bytes back
  procedure spi_xfer(
    constant tx_bytes : in byte_array;
    variable rx_bytes : out byte_array;
    signal sck  : inout std_logic;
    signal mosi : inout std_logic;
    signal miso : in    std_logic;
    signal cs   : inout std_logic
  ) is
    variable b : std_logic_vector(7 downto 0);
    constant HALF : time := 100 ns;  -- 5 MHz SCK
  begin
    cs <= '0';
    wait for HALF;
    for i in tx_bytes'range loop
      for j in 7 downto 0 loop
        mosi <= tx_bytes(i)(j);
        sck  <= '1'; wait for HALF;
        b(j) := miso;
        sck  <= '0'; wait for HALF;
      end loop;
      rx_bytes(i) := b;
    end loop;
  end;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.OLS_Interface(behavioral)
    generic map (
      Baud_Rate    => 921600,
      CLK_Frequency => 24000000,
      Max_Samples  => 1048576,
      OS_Rate      => 13,
      Def_IFace    => 1
    )
    port map (
      CLK => clk,
      FAST_CLK => clk,       -- Connect fast_clk to sys_clk for simulation
      UART_RX => spi_sck,    -- SPI_SCK on UART_RX pin
      UART_TX => open,
      SPI_CS  => spi_cs,
      SPI_MOSI => spi_mosi,
      SPI_MISO => spi_miso,
      Interface_Mode => int_mode,
      Inputs => (others => '0'),
      Rate_Div => open,
      Samples => open,
      Start_Offset => open,
      Run => open,
      Full => '0',
      Address => open,
      Outputs => (others => '0'),
      Gen_Busy => '0',
      Armed => open,
      Fast_Mode => open,
      Continuous_Mode => open,
      Buffer_Full => "000",
      Buffer_Ack => open
    );

  process
    variable rx : byte_array(0 to 4) := (others => (others => '0'));
    variable rx2 : byte_array(0 to 4) := (others => (others => '0'));
    variable rate_div_val : natural := 0;
    variable read_count_val : natural := 0;
  begin
    wait for 10 us;

    -- ============================================================
    -- tc_divider: CMD_DIVIDER over SPI, then verify via CMD_STATUS
    -- ============================================================
    if TEST = "all" or TEST = "tc_divider" then
      report "--- tc_divider: CMD_DIVIDER(23) via SPI ---" severity note;

      -- Send CMD_DIVIDER (0x80) with 0x00000017 = 23
      spi_xfer((0 => x"80", 1 => x"17", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      -- Read back via CMD_STATUS (0x03): response is pipelined
      -- Send status cmd, then dummy to get pipelined response
      spi_xfer((0 => x"03", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      -- Dummy transaction reads back the status bytes
      spi_xfer((0 => x"11", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx2, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      -- rx2(0) = preamble, rx2(1..4) = status response bytes
      -- Status: byte1 = Read_Count mod 256, byte2 = Read_Count/256, byte3 = Rate_Div mod 256
      rate_div_val := to_integer(unsigned(rx2(3)));  -- 4th byte in the response (index 3)
      report "tc_divider: Rate_Div = " & integer'image(rate_div_val) severity note;

      if rate_div_val = 23 then
        report "tc_divider: Divider correctly set to 23 PASS" severity note;
      else
        report "tc_divider: Divider should be 23 but got " & integer'image(rate_div_val) & " FAIL" severity failure;
      end if;
    end if;

    -- ============================================================
    -- tc_readcount: CMD_RCOUNT over SPI, verify via CMD_STATUS
    -- ============================================================
    if TEST = "all" or TEST = "tc_readcount" then
      report "--- tc_readcount: CMD_RCOUNT(5000) via SPI ---" severity note;

      -- Reset
      spi_xfer((0 => x"00", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      -- Send CMD_RCOUNT (0x84) with 5000 = 0x00001388
      spi_xfer((0 => x"84", 1 => x"88", 2 => x"13", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      -- Read back via pipelined status
      spi_xfer((0 => x"03", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;
      spi_xfer((0 => x"11", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx2, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      read_count_val := to_integer(unsigned(rx2(2))) * 256 + to_integer(unsigned(rx2(1)));
      rate_div_val := to_integer(unsigned(rx2(3)));
      report "tc_readcount: Read_Count = " & integer'image(read_count_val) &
             ", Rate_Div = " & integer'image(rate_div_val) severity note;

      if read_count_val = 5000 then
        report "tc_readcount: Read_Count correctly set to 5000 PASS" severity note;
      else
        report "tc_readcount: Read_Count should be 5000 but got " & integer'image(read_count_val) & " FAIL" severity failure;
      end if;
    end if;

    -- ============================================================
    -- tc_divider_fix: CMD_DIVIDER with Thread44=6 (original) vs Thread44=2 (fix)
    -- We check whether data accumulation happens
    -- ============================================================
    if TEST = "all" or TEST = "tc_divider_fix" then
      report "--- tc_divider_fix: Check if CMD_DIVIDER data is stored ---" severity note;

      -- Reset
      spi_xfer((0 => x"00", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;

      -- Send CMD_DIVIDER with value 42 = 0x2A (distinct, not 0, not from stale data)
      spi_xfer((0 => x"80", 1 => x"2A", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;

      -- Read back Divider via status: send CMD_STATUS then dummy
      spi_xfer((0 => x"03", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;
      spi_xfer((0 => x"11", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 2 us;

      report "Dummy MISO bytes:" severity note;
      for i in 0 to 4 loop
        report "  rx(" & integer'image(i) & ") = 0x" & to_hstring(rx(i)) severity note;
      end loop;

      -- rx(4) is Rate_Div mod 256 (the 5th byte of dummy response)
      -- Note: Rate_Div = Divider + 1, so Divider=42 => Rate_Div=43
      rate_div_val := to_integer(unsigned(rx(4)));
      report "tc_divider_fix: Rate_Div readback = " & integer'image(rate_div_val) &
             ", Divider = " & integer'image(rate_div_val - 1) severity note;

      if rate_div_val = 43 then
        report "tc_divider_fix: Divider=42 correctly stored PASS" severity note;
      else
        report "tc_divider_fix: Expected Rate_Div=43 (Divider=42) but got " & integer'image(rate_div_val) & " FAIL" severity failure;
      end if;
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;
