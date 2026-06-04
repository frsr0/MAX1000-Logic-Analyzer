library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_spi_slave is
  generic (TEST : string := "tc_basic");
end tb_spi_slave;

architecture sim of tb_spi_slave is
  constant CLK_PERIOD : time := 20.833 ns;  -- 48 MHz
  constant SCK_PERIOD : time := 200 ns;     -- 5 MHz (standard)

  signal sys_clk  : std_logic := '0';
  signal reset    : std_logic := '0';
  signal SCK      : std_logic := '0';
  signal MOSI     : std_logic := '0';
  signal MISO     : std_logic;
  signal CS_n     : std_logic := '1';
  signal TX_Data  : std_logic_vector(7 downto 0) := (others => '0');
  signal TX_Ready : std_logic;
  signal RX_Data  : std_logic_vector(7 downto 0);
  signal RX_Valid : std_logic;

  -- Full-duplex SPI transfer: send tx_byte on MOSI, receive rx_byte from MISO
  procedure spi_xfer(
    signal sck      : inout std_logic;
    signal mosi     : inout std_logic;
    signal miso     : in    std_logic;
    signal cs_n     : inout std_logic;
    constant mosi_data : in  std_logic_vector(7 downto 0);
    variable miso_data : out std_logic_vector(7 downto 0);
    constant period    : in  time := SCK_PERIOD
  ) is
    constant half : time := period / 2;
  begin
    cs_n <= '0';
    wait for half;
    for i in 7 downto 0 loop
      mosi <= mosi_data(i);
      sck <= '1';              -- rising edge: slave samples MOSI
      wait for half;
      miso_data(i) := miso;    -- capture MISO on falling edge boundary
      sck <= '0';              -- falling edge: slave shifts MISO
      wait for half;
    end loop;
    cs_n <= '1';
    wait for period;
  end procedure;

begin
  sys_clk <= not sys_clk after CLK_PERIOD / 2;

  uut: entity work.SPI_Slave
    port map (
      sys_clk  => sys_clk,
      reset    => reset,
      SCK      => SCK,
      MOSI     => MOSI,
      MISO     => MISO,
      CS_n     => CS_n,
      TX_Data  => TX_Data,
      TX_Ready => TX_Ready,
      RX_Data  => RX_Data,
      RX_Valid => RX_Valid
    );

  stimuli: process
    variable rx_byte : std_logic_vector(7 downto 0);
  begin
    reset <= '1';
    wait for 100 ns;
    reset <= '0';
    wait for 200 ns;

    if TEST = "tc_basic" then
      report "=== tc_basic: single byte full-duplex ===";
      TX_Data <= x"5A";
      wait for CLK_PERIOD * 3;
      spi_xfer(SCK, MOSI, MISO, CS_n, x"A5", rx_byte);
      wait for 1 us;
      if RX_Data = x"A5" then
        report "tc_basic: RX match A5 PASS" severity note;
      else
        report "tc_basic: RX mismatch, expected A5 got " & to_hstring(RX_Data) severity error;
      end if;
      if rx_byte = x"5A" then
        report "tc_basic: MISO match 5A PASS" severity note;
      else
        report "tc_basic: MISO mismatch, expected 5A got " & to_hstring(rx_byte) severity error;
      end if;
      wait for 1 us;

    elsif TEST = "tc_duplex" then
      report "=== tc_duplex: verify both directions simultaneously ===";
      TX_Data <= x"AA";
      wait for CLK_PERIOD * 3;
      spi_xfer(SCK, MOSI, MISO, CS_n, x"55", rx_byte);
      wait for 200 ns;
      if RX_Data = x"55" and rx_byte = x"AA" then
        report "tc_duplex: both directions match PASS" severity note;
      else
        report "tc_duplex: fail RX=" & to_hstring(RX_Data) & " MISO=" & to_hstring(rx_byte) severity error;
      end if;
      wait for 1 us;

    elsif TEST = "tc_multi_byte" then
      report "=== tc_multi_byte: 8 consecutive transfers ===";
      for i in 0 to 7 loop
        TX_Data <= std_logic_vector(to_unsigned(i * 17 + 10, 8));
        wait for CLK_PERIOD * 3;
        spi_xfer(SCK, MOSI, MISO, CS_n,
                 std_logic_vector(to_unsigned(i * 33 + 16, 8)), rx_byte);
        wait for CLK_PERIOD * 2;
      end loop;
      wait for 1 us;

    elsif TEST = "tc_cs_abort" then
      report "=== tc_cs_abort: CS# deassert mid-byte, then full xfer ===";
      TX_Data <= x"FF";
      wait for CLK_PERIOD * 3;
      CS_n <= '0';
      wait for 50 ns;
      SCK <= '1'; wait for SCK_PERIOD/2;
      SCK <= '0'; wait for SCK_PERIOD/2;
      SCK <= '1'; wait for SCK_PERIOD/2;
      CS_n <= '1';  -- abort after 2 bits
      wait for 500 ns;
      SCK <= '0';
      wait for 200 ns;
      TX_Data <= x"55";
      wait for CLK_PERIOD * 3;
      spi_xfer(SCK, MOSI, MISO, CS_n, x"AA", rx_byte);
      wait for 1 us;

    elsif TEST = "tc_high_speed" then
      report "=== tc_high_speed: 30 MHz SPI ===";
      TX_Data <= x"55";
      wait for CLK_PERIOD * 3;
      spi_xfer(SCK, MOSI, MISO, CS_n, x"AA", rx_byte, 33.3 ns);
      wait for 1 us;
      if RX_Data = x"AA" then
        report "tc_high_speed: 30 MHz RX match PASS" severity note;
      else
        report "tc_high_speed: fail RX=" & to_hstring(RX_Data) severity error;
      end if;
      TX_Data <= x"AA";
      wait for CLK_PERIOD * 3;
      spi_xfer(SCK, MOSI, MISO, CS_n, x"55", rx_byte, 33.3 ns);
      wait for 1 us;

    else
      report "Unknown test: " & TEST severity failure;
    end if;

    report "Test " & TEST & " complete" severity note;
    wait;
  end process;
end sim;
