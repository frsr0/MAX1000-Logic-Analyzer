library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_gen_strt is
end tb_gen_strt;

architecture sim of tb_gen_strt is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant SPI_HALF   : time := 500 ns;     -- 1 MHz SPI
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal running  : boolean := true;

  signal spi_cs   : std_logic := '1';
  signal spi_mosi : std_logic := '0';
  signal spi_sck  : std_logic := '0';  -- goes through UART_RX
  signal uart_rx  : std_logic := '1';

  signal gen_start : std_logic;
  signal gen_busy  : std_logic := '0';

  signal all_pass  : boolean := true;
  signal gen_start_captured : std_logic := '0';
begin

  -- Capture rising edge of gen_start (pulse is only ~125ns)
  process(gen_start)
  begin
    if rising_edge(gen_start) then
      gen_start_captured <= '1';
    end if;
  end process;
  clk <= not clk after CLK_PERIOD / 2 when running;
  fast_clk <= not fast_clk after 4.166 ns when running;

  -- SCK toggles when CS is active
  uart_rx <= spi_sck;

  DUT : entity work.OLS_Interface
    generic map (CLK_Frequency => 24000000, Def_IFace => 1)
    port map (
      CLK => clk,
      FAST_CLK => fast_clk,
      UART_RX => uart_rx,
      SPI_CS => spi_cs,
      SPI_MOSI => spi_mosi,
      Inputs => (others => '0'),
      Outputs => (others => '0'),
      Buffer_Full => "000",
      Gen_Start => gen_start,
      Gen_Busy => gen_busy
    );

  -- SPI master process
  process
    procedure spi_byte(byte : std_logic_vector(7 downto 0)) is
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for i in 7 downto 0 loop
        spi_mosi <= byte(i);
        spi_sck <= '0';
        wait for SPI_HALF;
        spi_sck <= '1';
        wait for SPI_HALF;
      end loop;
      spi_sck <= '0';
      spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;
  begin
    report "=== Test: GEN_STRT Dispatch ===" severity note;
    wait for 10 us;

    -- Send CMD_RESET (0x00) to put in known state
    report "Sending CMD_RESET (0x00)..." severity note;
    spi_byte(x"00");
    wait for 5 us;

    -- Check Gen_Start is LOW after reset
    if gen_start = '1' then
      report "FAIL: Gen_Start is HIGH after reset" severity error;
      all_pass <= false;
    end if;

    -- Send CMD_GEN_STRT (0xA1) as single SPI byte
    report "Sending CMD_GEN_STRT (0xA1)..." severity note;
    spi_byte(x"A1");

    wait for 30 us;
    
    if gen_start_captured = '1' then
      report "PASS: Gen_Start pulsed HIGH after 0xA1" severity note;
    else
      report "FAIL: Gen_Start never pulsed HIGH after 0xA1" severity error;
      all_pass <= false;
    end if;

    if all_pass then
      report "*** TEST PASSED ***" severity note;
    else
      report "*** TEST FAILED ***" severity error;
    end if;

    running <= false;
    wait;
  end process;
end sim;
