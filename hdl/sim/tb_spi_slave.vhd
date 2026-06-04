library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_spi_slave is
  generic (
    CLK_FREQ  : natural := 96000000;
    FAST_FREQ : natural := 120000000;
    SPI_HALF  : time := 50 ns  -- 10 MHz SCK
  );
end tb_spi_slave;

architecture bench of tb_spi_slave is
  constant CLK_PERIOD  : time := 1 sec / real(CLK_FREQ);
  constant FAST_PERIOD : time := 1 sec / real(FAST_FREQ);

  signal sys_clk  : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal reset    : std_logic := '0';
  signal sck      : std_logic := '0';
  signal mosi     : std_logic := '0';
  signal miso     : std_logic;
  signal cs_n     : std_logic := '1';
  signal tx_data  : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_ready : std_logic;
  signal rx_data  : std_logic_vector(7 downto 0);
  signal rx_valid : std_logic;

  signal rx_valid_captured : std_logic := '0';
begin

  gen_clk(sys_clk, CLK_PERIOD / 2);
  gen_clk(fast_clk, FAST_PERIOD / 2);

  DUT : entity work.SPI_Slave2
    port map (
      sys_clk    => sys_clk,
      fast_clk   => fast_clk,
      reset      => reset,
      SCK        => sck,
      MOSI       => mosi,
      MISO       => miso,
      CS_n       => cs_n,
      TX_Data    => tx_data,
      SPI_Preamble => x"00",
      TX_Ready   => tx_ready,
      PipeDepth  => 8,
      RX_Data    => rx_data,
      RX_Valid   => rx_valid
    );

  -- Capture rising edge of rx_valid
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if rx_valid = '1' then
        rx_valid_captured <= '1';
      end if;
    end if;
  end process;

  process
    variable rx_byte : std_logic_vector(7 downto 0);
    constant TX_BYTE : std_logic_vector(7 downto 0) := x"A5";
  begin
    reset <= '1';
    wait_cycles(sys_clk, 10);
    reset <= '0';
    wait_cycles(sys_clk, 10);

    report "=== Single byte full-duplex @ 10MHz SCK ===";

    tx_data <= x"5A";
    wait_cycles(sys_clk, 5);

    -- SPI transaction: send A5, expect MISO preamble (0x00)
    cs_n <= '0';
    wait for 500 ns;  -- CS setup

    for b in 7 downto 0 loop
      sck <= '0';
      mosi <= TX_BYTE(b);
      wait for 50 ns;
      sck <= '1';
      rx_byte(b) := miso;
      wait for 50 ns;
    end loop;

    sck <= '0';
    wait for 100 ns;
    report "MISO: " & to_hstring(rx_byte);
    cs_n <= '1';

    wait_cycles(sys_clk, 100);
    report "rx_valid_captured=" & std_logic'image(rx_valid_captured);
    report "rx_valid=" & std_logic'image(rx_valid);
    report "rx_data=" & to_hstring(rx_data);

    check(rx_valid_captured = '1', "RX_Valid never captured");
    check(rx_data = x"A5", "RX data mismatch: expected A5, got " & to_hstring(rx_data));

    report "=== ALL SPI SLAVE TESTS PASSED ===";
    wait;
  end process;

end bench;
