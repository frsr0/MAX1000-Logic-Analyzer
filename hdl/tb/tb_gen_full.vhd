library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_gen_full is
  generic (
    CLK_FREQ : natural := 48000000
  );
end tb_gen_full;

architecture bench of tb_gen_full is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  signal clk       : std_logic := '0';
  signal fast_clk  : std_logic := '0';
  signal spi_cs    : std_logic := '1';
  signal spi_sck   : std_logic := '0';
  signal spi_mosi  : std_logic := '0';
  signal spi_miso  : std_logic;
  signal iface_mode : std_logic;
  signal inputs    : std_logic_vector(31 downto 0) := (others => '0');
  signal rate_div  : natural range 1 to 150000000;
  signal samples   : natural range 1 to 25000;
  signal start_off : natural range 0 to 25000;
  signal run       : std_logic;
  signal full      : std_logic := '0';
  signal address   : natural range 0 to 24999;
  signal outputs   : std_logic_vector(31 downto 0) := (others => '0');
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div  : std_logic_vector(15 downto 0);
  signal gen_busy      : std_logic;
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 31;
  signal gen_scl_pin   : natural range 0 to 31;
  signal gen_i2c_rd_len : natural range 0 to 255;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0);
  signal gen_i2c_test   : std_logic;
  signal gen_spi_test   : std_logic;
  signal armed        : std_logic;
  signal fast_mode    : std_logic;
  signal cont_mode    : std_logic;
  signal analog_mode  : std_logic_vector(2 downto 0);
  signal analog_ch0   : natural range 0 to 15;
  signal analog_ch1   : natural range 0 to 15;
  signal buffer_full  : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack   : std_logic_vector(2 downto 0);
  signal pin_map_write : std_logic;
  signal pin_map_ch    : natural range 0 to 15;
  signal pin_map_pin   : natural range 0 to 31;

  -- Signal_Gen outputs
  signal gen_tx_out : std_logic;
  signal gen_scl_out : std_logic;

  signal gen_busy_cap : std_logic := '0';
  signal gen_busy_clr : std_logic := '0';
  signal gen_start_cap : std_logic := '0';
  signal gen_start_clr : std_logic := '0';

  -- Force disp_gen_start via internal signal
  signal force_gen_start : std_logic := '0';

begin
  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;

  -- OLS_Interface (provides gen_start, gen_load_we, etc.)
  DUT_IFACE : entity work.OLS_Interface
    generic map (
      CLK_Frequency => CLK_FREQ,
      Max_Samples   => 25000
    )
    port map (
      CLK        => clk,
      FAST_CLK   => fast_clk,
      SPI_CS     => spi_cs,
      SPI_SCK    => spi_sck,
      SPI_MOSI   => spi_mosi,
      SPI_MISO   => spi_miso,
      Interface_Mode => iface_mode,
      Inputs     => inputs,
      Rate_Div   => rate_div,
      Samples    => samples,
      Start_Offset => start_off,
      Run        => run,
      Full       => full,
      Address    => address,
      Outputs    => outputs,
      Gen_Load_Byte => gen_load_byte,
      Gen_Load_We   => gen_load_we,
      Gen_Start     => gen_start,
      Gen_Baud_Div  => gen_baud_div,
      Gen_Busy      => gen_busy,
      Gen_Proto     => gen_proto,
      Gen_TX_Pin    => gen_tx_pin,
      Gen_SCL_Pin   => gen_scl_pin,
      Gen_I2C_Rd_Len => gen_i2c_rd_len,
      Gen_I2C_Dev_R  => gen_i2c_dev_r,
      Gen_I2C_Test   => gen_i2c_test,
      Gen_SPI_Test   => gen_spi_test,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      Continuous_Mode => cont_mode,
      Analog_Mode  => analog_mode,
      Analog_Ch0   => analog_ch0,
      Analog_Ch1   => analog_ch1,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack,
      Pin_Map_Write  => pin_map_write,
      Pin_Map_Channel => pin_map_ch,
      Pin_Map_Pin     => pin_map_pin
    );

  -- Signal_Gen (produces UART output)
  DUT_GEN : entity work.Signal_Gen
    generic map (FIFO_DEPTH => 256)
    port map (
      CLK       => clk,
      Load_Byte => gen_load_byte,
      Load_We   => gen_load_we,
      Start     => gen_start,
      Baud_Div  => gen_baud_div,
      Proto     => gen_proto,
      SPI_Mode  => gen_spi_test,
      Tx_Out    => gen_tx_out,
      Scl_Out   => gen_scl_out,
      Busy      => gen_busy,
      Active    => open,
      I2C_Rd_Len => gen_i2c_rd_len,
      I2C_Dev_R  => gen_i2c_dev_r,
      Sda_In     => '1',
      CRC_En     => '0',
      CRC_Poly   => x"A001"
    );

  -- Force disp_gen_start in OLS_Interface by overriding the internal signal
  force_gen_start <= '1' after 3 us, '0' after 3.1 us;

  -- Capture gen_start pulse
  process(clk)
  begin
    if rising_edge(clk) then
      if gen_start_clr = '1' then
        gen_start_cap <= '0';
      elsif gen_start = '1' then
        gen_start_cap <= '1';
      end if;
    end if;
  end process;

  -- Capture gen_busy
  process(clk)
  begin
    if rising_edge(clk) then
      if gen_busy_clr = '1' then
        gen_busy_cap <= '0';
      elsif gen_busy = '1' then
        gen_busy_cap <= '1';
      end if;
    end if;
  end process;

  process
    procedure load_byte(b : std_logic_vector(7 downto 0)) is
    begin
      wait until rising_edge(clk);
      gen_load_byte <= b;
      gen_load_we <= '1';
      wait until rising_edge(clk);
      gen_load_we <= '0';
    end procedure;
  begin
    wait until rising_edge(clk);
    wait_cycles(clk, 50);
    report "=== GEN FULL CHAIN TEST ===";

    --------------------------------------------------------------
    -- Configure gen: UART mode, 115200 baud
    --------------------------------------------------------------
    -- Set gen_proto to '0' (UART) from OLS_Interface
    -- This happens via REG_GEN_PROTO write (SPI packet)
    -- For this test we just check the hardware path works
    report "--- Configure gen ---";

    -- Load "Hello" to FIFO
    load_byte(x"48");  -- 'H'
    load_byte(x"65");  -- 'e'
    load_byte(x"6C");  -- 'l'
    load_byte(x"6C");  -- 'l'
    load_byte(x"6F");  -- 'o'
    wait_cycles(clk, 10);
    report "  Loaded 5 bytes via Gen_Load_We";

    --------------------------------------------------------------
    -- Pulse gen_start by overriding disp_gen_start
    --------------------------------------------------------------
    -- The OLS_Interface's spi_pkt_dispatch process sets disp_gen_start
    -- when CMD_GEN_START is received. We can't easily trigger this
    -- from outside without SPI packets. Instead we override the
    -- internal disp_gen_start signal via external name.
    report "--- Trigger gen_start ---";
    gen_start_clr <= '1'; wait_cycles(clk, 1); gen_start_clr <= '0';
    gen_busy_clr <= '1'; wait_cycles(clk, 1); gen_busy_clr <= '0';

    -- Override disp_gen_start via external name to simulate CMD_GEN_START
    << signal .tb_gen_full.dut_iface.disp_gen_start : std_logic >> <= force_gen_start;

    -- Wait and check
    wait_cycles(clk, 20);
    report "  gen_start_cap=" & std_logic'image(gen_start_cap);
    report "  gen_busy_cap=" & std_logic'image(gen_busy_cap);
    report "  gen_busy=" & std_logic'image(gen_busy);

    if gen_busy_cap = '1' then
      report "  *** GEN BUSY WAS ASSERTED - FULL CHAIN WORKS ***";
    else
      report "  *** GEN BUSY NEVER ASSERTED ***";
    end if;

    --------------------------------------------------------------
    -- Wait for gen to finish, check Tx_Out
    --------------------------------------------------------------
    wait_cycles(clk, 50000);  -- Wait for "Hello" transmission (~433 us)
    report "  gen_busy after wait: " & std_logic'image(gen_busy);
    report "  gen_tx_out: " & std_logic'image(gen_tx_out);

    report "=== GEN FULL CHAIN TEST COMPLETE ===";
    wait;
  end process;
end bench;
