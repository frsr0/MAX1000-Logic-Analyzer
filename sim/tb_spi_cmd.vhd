library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_spi_cmd is
  generic (TEST : string := "tc_cmd_id");
end tb_spi_cmd;

architecture sim of tb_spi_cmd is
  constant CLK_PERIOD : time := 20.833 ns;
  constant SCK_PERIOD : time := 2 us;
  constant HALF_SCK   : time := SCK_PERIOD / 2;

  signal clk       : std_logic := '0';
  signal running   : boolean := true;

  -- OLS_Interface ports: SCK = UART_RX, MOSI = SPI_MOSI, CS = SPI_CS
  signal sck       : std_logic := '0';  -- SPI mode 0 (CPOL=0): idle low
  signal mosi      : std_logic := '0';
  signal miso      : std_logic;
  signal cs_n      : std_logic := '1';
  signal uart_tx   : std_logic;
  signal int_mode  : std_logic;

  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  -- Full-duplex SPI: send tx bytes on MOSI, collect rx bytes from MISO
  -- One SCK cycle per bit: rising edge (sample MISO), falling edge (change MISO)
  -- SCK starts low (CPOL=0), each byte uses 8 complete SCK cycles
  procedure spi_xfer(
    constant tx     : in    byte_array;
    variable rx     : out   byte_array;
    signal sck_sig  : inout std_logic;
    signal mosi_sig : inout std_logic;
    signal miso_sig : in    std_logic;
    signal cs_sig   : inout std_logic
  ) is
    variable b : std_logic_vector(7 downto 0);
  begin
    cs_sig <= '0';
    wait for HALF_SCK;  -- CS setup time
    for i in tx'range loop
      for j in 7 downto 0 loop
        mosi_sig <= tx(i)(j);      -- set MOSI before rising edge
        sck_sig <= '1';             -- rising edge: slave samples MOSI
        wait for HALF_SCK;
        b(j) := miso_sig;           -- read MISO (stable since last falling edge)
        sck_sig <= '0';             -- falling edge: slave shifts MISO
        wait for HALF_SCK;
      end loop;
      rx(i) := b;
    end loop;
  end procedure;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.OLS_Interface(behavioral)
    generic map (
      Baud_Rate    => 12000000,
      CLK_Frequency => 48000000,
      Max_Samples  => 1048576,
      OS_Rate      => 13,
      Def_IFace    => 1
    )
    port map (
      CLK => clk,
      UART_RX => sck,       -- SCK shares UART_RX pin
      UART_TX => uart_tx,
      SPI_CS  => cs_n,
      SPI_MOSI => mosi,
      SPI_MISO => miso,
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
    variable rx : byte_array(0 to 9) := (others => (others => '0'));
  begin
    wait for 10 us;

    if TEST = "all" or TEST = "tc_cmd_id" then
      report "--- tc_cmd_id: CMD_ID over SPI ---" severity note;

      -- Send CMD_ID (0x02) followed by 9 dummy bytes to flush the pipeline
      -- Full duplex: MOSI bytes go in, MISO bytes come out simultaneously
      spi_xfer(
        (0 => x"02", 1 => x"00", 2 => x"00", 3 => x"00",
         4 => x"00", 5 => x"00", 6 => x"00", 7 => x"00",
         8 => x"00", 9 => x"00"),
        rx, sck, mosi, miso, cs_n);

      cs_n <= '1';
      wait for 1 us;

      report "MISO: "
        & to_hstring(rx(0)) & " " & to_hstring(rx(1)) & " "
        & to_hstring(rx(2)) & " " & to_hstring(rx(3)) & " "
        & to_hstring(rx(4)) & " " & to_hstring(rx(5)) & " "
        & to_hstring(rx(6)) & " " & to_hstring(rx(7)) & " "
        & to_hstring(rx(8)) & " " & to_hstring(rx(9))
        severity note;

      -- Look for "1ALS" (0x31, 0x41, 0x4c, 0x53) at any offset
      for shift in 0 to 6 loop
        if rx(shift)   = x"31" and rx(shift+1) = x"41"
           and rx(shift+2) = x"4c" and rx(shift+3) = x"53" then
          report "FOUND '1ALS' at offset " & integer'image(shift) & " PASS" severity note;
          exit;
        end if;
      end loop;

      -- Report individual ID byte locations
      for i in 0 to 9 loop
        if rx(i) = x"31" then report "  '1'(0x31) at offset " & integer'image(i) severity note; end if;
        if rx(i) = x"41" then report "  'A'(0x41) at offset " & integer'image(i) severity note; end if;
        if rx(i) = x"4c" then report "  'L'(0x4c) at offset " & integer'image(i) severity note; end if;
        if rx(i) = x"53" then report "  'S'(0x53) at offset " & integer'image(i) severity note; end if;
      end loop;
    end if;

    running <= false;
    wait;
  end process;

end sim;
