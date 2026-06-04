library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_full_path is
end tb_full_path;

architecture sim of tb_full_path is
  constant CLK_PERIOD : time := 41.667 ns;
  constant SPI_HALF   : time := 500 ns;
  signal clk : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal running : boolean := true;

  signal spi_cs   : std_logic := '1';
  signal spi_mosi : std_logic := '0';
  signal spi_sck  : std_logic := '0';
  signal uart_rx  : std_logic := '1';

  signal gen_start : std_logic;
  signal gen_busy  : std_logic;
  signal gen_tx    : std_logic;
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we  : std_logic;
  signal gen_baud_div : std_logic_vector(15 downto 0);

  signal gen_start_captured : std_logic := '0';
  signal gen_busy_captured  : std_logic := '0';
begin
  clk <= not clk after CLK_PERIOD / 2 when running;
  fast_clk <= not fast_clk after 4.166 ns when running;
  uart_rx <= spi_sck;

  -- Core: connects OLS_Interface → Signal_Gen
  core : entity work.OLS_Logic_Analyzer
    generic map (CLK_Frequency => 24000000, Def_IFace => 1)
    port map (
      CLK => clk,
      FAST_CLK => fast_clk,
      UART_RX => uart_rx,
      SPI_CS => spi_cs,
      SPI_MOSI => spi_mosi,
      Gen_Start => gen_start,
      Gen_Busy => gen_busy,
      Gen_Load_Byte => gen_load_byte,
      Gen_Load_We => gen_load_we,
      Gen_Baud_Div => gen_baud_div
    );

  -- Signal_Gen: receives Start from core's Gen_Start
  gen : entity work.Signal_Gen
    port map (
      CLK => clk,
      Load_Byte => gen_load_byte,
      Load_We => gen_load_we,
      Start => gen_start,
      Baud_Div => gen_baud_div,
      Tx_Out => gen_tx,
      Busy => gen_busy
    );

  -- Capture gen_start pulse (short)
  process(gen_start)
  begin
    if rising_edge(gen_start) then
      gen_start_captured <= '1';
    end if;
  end process;

  -- Capture gen_busy going high
  process(gen_busy)
  begin
    if rising_edge(gen_busy) then
      gen_busy_captured <= '1';
    end if;
  end process;

  -- SPI master
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
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;
  begin
    report "=== Full Path Integration Test ===" severity note;
    wait for 10 us;

    -- Reset
    report "Sending CMD_RESET..." severity note;
    spi_byte(x"00");
    wait for 5 us;

    -- Load 'H' into FIFO via CMD_GEN_LOAD (0xA0)
    report "Loading 'H' (0x48) via CMD_GEN_LOAD..." severity note;
    spi_byte(x"A0");
    spi_byte(x"48");  -- data byte = 'H'
    spi_byte(x"00");  -- padding
    spi_byte(x"00");
    spi_byte(x"00");
    wait for 5 us;

    -- Set Baud_Div to 208 via CMD_GEN_BAUD (0xA2)
    report "Setting baud rate..." severity note;
    spi_byte(x"A2");
    spi_byte(x"D0");  -- 208 LSB
    spi_byte(x"00");  -- 208 MSB
    spi_byte(x"00");
    spi_byte(x"00");
    wait for 5 us;

    -- Send GEN_STRT (0xA1)
    report "Sending GEN_STRT..." severity note;
    spi_byte(x"A1");
    wait for 20 us;

    -- Check results
    if gen_start_captured = '1' then
      report "PASS: Gen_Start pulsed" severity note;
    else
      report "FAIL: Gen_Start never pulsed" severity error;
    end if;

    if gen_busy_captured = '1' then
      report "PASS: Gen_Busy went high (gen started)" severity note;
    else
      report "FAIL: Gen_Busy never went high (gen didn't start)" severity error;
    end if;

    running <= false;
    wait;
  end process;
end sim;
