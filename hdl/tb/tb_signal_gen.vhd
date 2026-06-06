library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_signal_gen is
  generic (
    CLK_FREQ : natural := 96000000;
    BAUD_DIV : natural := 834  -- ~115200 @ 96 MHz
  );
end tb_signal_gen;

architecture bench of tb_signal_gen is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant BAUD_TIME  : time := 1 sec / real(CLK_FREQ / BAUD_DIV);

  signal clk       : std_logic := '0';
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we   : std_logic := '0';
  signal start     : std_logic := '0';
  signal baud_div_s : std_logic_vector(15 downto 0) := std_logic_vector(to_unsigned(BAUD_DIV, 16));
  signal proto     : std_logic := '0';
  signal spi_mode  : std_logic := '0';
  signal tx_out    : std_logic;
  signal scl_out   : std_logic;
  signal busy      : std_logic;
  signal active    : std_logic;
  signal i2c_rd_len : natural range 0 to 255 := 0;
  signal i2c_dev_r  : std_logic_vector(7 downto 0) := (others => '0');
  signal sda_in     : std_logic := '1';
  signal crc_en     : std_logic := '0';
  signal crc_poly   : std_logic_vector(15 downto 0) := x"A001";

  signal running : boolean := true;

  procedure load_fifo(
    signal sclk : in std_logic;
    signal we  : out std_logic;
    signal data : out std_logic_vector(7 downto 0);
    constant bytes : in byte_array
  ) is
  begin
    for i in bytes'range loop
      wait until rising_edge(sclk);
      data <= bytes(i);
      we <= '1';
      wait until rising_edge(sclk);
      we <= '0';
    end loop;
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.Signal_Gen
    generic map (FIFO_DEPTH => 256)
    port map (
      CLK        => clk,
      Load_Byte  => load_byte,
      Load_We    => load_we,
      Start      => start,
      Baud_Div   => baud_div_s,
      Proto      => proto,
      SPI_Mode   => spi_mode,
      Tx_Out     => tx_out,
      Scl_Out    => scl_out,
      Busy       => busy,
      Active     => active,
      I2C_Rd_Len => i2c_rd_len,
      I2C_Dev_R  => i2c_dev_r,
      Sda_In     => sda_in,
      CRC_En     => crc_en,
      CRC_Poly   => crc_poly
    );

  process
    variable cycles : natural;
    variable found  : boolean;
  begin
    report "=== Signal Generator tests ===";

    ------------------------------------------------------------------
    -- Test 1: UART TX - single byte
    ------------------------------------------------------------------
    report "Test 1: UART TX single byte";
    load_fifo(clk, load_we, load_byte, (0 => x"55"));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 100 us, "Generator should go busy");

    -- Verify start bit
    measure_pulse(clk, tx_out, '0', BAUD_DIV * 2, cycles, found);
    check(found, "Start bit not found");
    report "Start bit measured: " & integer'image(cycles) & " cycles (expected ~" & integer'image(BAUD_DIV) & ")";

    wait_until(clk, busy, '0', 500 us, "Generator should finish");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: UART TX - multiple bytes (5 bytes "Hello")
    ------------------------------------------------------------------
    report "Test 2: UART TX 5 bytes";
    load_fifo(clk, load_we, load_byte, (
      0 => x"48",  -- 'H'
      1 => x"65",  -- 'e'
      2 => x"6C",  -- 'l'
      3 => x"6C",  -- 'l'
      4 => x"6F"   -- 'o'
    ));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '0', 1 ms, "Generator 5 bytes timeout");
    check(busy = '0', "Generator should be idle after 5 bytes");
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: UART TX - FIFO depth (256 bytes)
    ------------------------------------------------------------------
    report "Test 3: UART TX 256 bytes (FIFO depth)";
    for i in 0 to 255 loop
      wait until rising_edge(clk);
      load_byte <= std_logic_vector(to_unsigned(i, 8));
      load_we <= '1';
      wait until rising_edge(clk);
      load_we <= '0';
    end loop;
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '0', 50 ms, "Generator 256 bytes timeout");
    check(busy = '0', "Generator should finish 256 bytes");
    report "Test 3: PASS";

    ------------------------------------------------------------------
    -- Test 4: SPI master
    ------------------------------------------------------------------
    report "Test 4: SPI master mode";
    spi_mode <= '1';
    load_fifo(clk, load_we, load_byte, (0 => x"55"));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 100 us, "SPI should go busy");
    -- Measure SCLK pulses
    measure_pulse(clk, scl_out, '1', BAUD_DIV * 2, cycles, found);
    check(found, "SPI SCLK high pulse not found");
    wait_until(clk, busy, '0', 1 ms, "SPI should finish");
    spi_mode <= '0';
    report "Test 4: PASS";

    ------------------------------------------------------------------
    -- Test 5: I2C master write to ADXL345
    ------------------------------------------------------------------
    report "Test 5: I2C master write (ADXL345 POWER_CTL)";
    proto <= '1';
    -- I2C write: dev_addr=0x53<<1, reg=0x2D, data=0x08
    -- First FIFO byte = 0xA6 (addr << 1 | W), then reg, then data
    load_fifo(clk, load_we, load_byte, (
      0 => x"A6",  -- 0x53 << 1 | W
      1 => x"2D",  -- POWER_CTL register
      2 => x"08"   -- measure mode
    ));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 100 us, "I2C should go busy");
    -- Should see START, 8 data bits per byte, ACK, STOP
    measure_pulse(clk, scl_out, '1', BAUD_DIV * 2, cycles, found);
    check(found, "I2C SCL high pulse not found");
    wait_until(clk, busy, '0', 1 ms, "I2C should finish");
    report "Test 5: PASS";

    ------------------------------------------------------------------
    -- Test 6: I2C master write then read (ADXL345 combined)
    ------------------------------------------------------------------
    report "Test 6: I2C combined write+read (ADXL345)";
    i2c_rd_len <= 6;  -- read 6 bytes (DATAX0..DATAZ1)
    i2c_dev_r  <= x"A7";  -- 0x53 << 1 | R
    -- Write phase: dev_W + register address
    load_fifo(clk, load_we, load_byte, (
      0 => x"A6",  -- 0x53 << 1 | W
      1 => x"32"   -- DATAX0 register
    ));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 100 us, "I2C combined should go busy");
    wait_until(clk, busy, '0', 10 ms, "I2C combined should finish");
    sda_in <= '1';
    proto <= '0';
    report "Test 6: PASS";

    ------------------------------------------------------------------
    -- Test 7: CRC-16 append
    ------------------------------------------------------------------
    report "Test 7: CRC-16 append";
    crc_en <= '1';
    load_fifo(clk, load_we, load_byte, (0 => x"01", 1 => x"02", 2 => x"03"));
    wait_cycles(clk, 5);
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '0', 5 ms, "CRC generator timeout");
    crc_en <= '0';
    report "Test 7: PASS";

    report "=== ALL SIGNAL GENERATOR TESTS PASSED ===";
    running <= false;
    wait;
  end process;

end bench;
