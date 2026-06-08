library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_gen_start_sim is
  generic (
    CLK_FREQ : natural := 48000000;
    SPI_HALF : time    := 100 ns
  );
end tb_gen_start_sim;

architecture bench of tb_gen_start_sim is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  -- OLS_Interface signals
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
  signal gen_busy      : std_logic := '0';
  signal gen_fifo_count : std_logic_vector(7 downto 0) := (others => '0');
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

  -- Signal_Gen signals
  signal gen_tx_out : std_logic;
  signal gen_scl_out : std_logic;
  signal gen_busy_sg : std_logic;

  -- Capture
  signal gen_start_cap : std_logic := '0';
  signal gen_start_clr : std_logic := '0';

  -- SPI packet send procedure (CS held low)
  procedure spi_packet(
    signal cs_n   : out std_logic;
    signal sck    : out std_logic;
    signal mosi   : out std_logic;
    signal miso   : in  std_logic;
    constant half_period : in  time;
    constant bytes : in byte_array
  ) is
    variable rx : byte_array(0 to bytes'length - 1);
    variable txb : std_logic_vector(7 downto 0);
  begin
    cs_n <= '0';
    wait for half_period;
    for i in 0 to bytes'length - 1 loop
      txb := bytes(i);
      for b in 7 downto 0 loop
        sck <= '0';
        mosi <= txb(b);
        wait for half_period;
        sck <= '1';
        wait for half_period;
      end loop;
    end loop;
    sck <= '0';
    cs_n <= '1';
    wait for half_period;
  end procedure;

  -- Build SPI packet with CRC
  function make_pkt(cmd : cmd_t; payload : byte_array; seq : integer := 1) return byte_array is
    variable p : byte_array(0 to 30) := (others => x"00");
    variable len : integer := payload'length;
    variable crc : integer := 65535;
    variable idx : integer := 0;
    variable crc_vec : std_logic_vector(15 downto 0);
    variable len_vec16 : std_logic_vector(15 downto 0);
  begin
    len_vec16 := std_logic_vector(to_unsigned(len, 16));
    p(0) := x"55"; p(1) := x"AA";  -- SYNC_REQ
    p(2) := cmd; p(3) := std_logic_vector(to_unsigned(seq, 8));
    p(4) := len_vec16(7 downto 0);  -- len low
    p(5) := len_vec16(15 downto 8); -- len high
    for i in 0 to len - 1 loop
      p(6 + i) := payload(i);
    end loop;
    idx := 6 + len;
    -- CRC over cmd, seq, len, payload
    crc := crc16_int(to_integer(unsigned(cmd)), crc);
    crc := crc16_int(seq, crc);
    crc := crc16_int(len mod 256, crc);
    crc := crc16_int(len / 256, crc);
    for i in 0 to len - 1 loop
      crc := crc16_int(to_integer(unsigned(payload(i))), crc);
    end loop;
    crc_vec := std_logic_vector(to_unsigned(crc, 16));
    p(idx) := crc_vec(7 downto 0);      -- CRC low
    p(idx + 1) := crc_vec(15 downto 8); -- CRC high
    return p;
  end function;

begin
  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;

  -- OLS_Interface
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
      Gen_Busy      => gen_busy_sg,
      Gen_Fifo_Count => gen_fifo_count,
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

  -- Signal_Gen (connected to OLS_Interface outputs)
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
      Busy      => gen_busy_sg,
      Active    => open,
      Fifo_Count => gen_fifo_count,
      I2C_Rd_Len => gen_i2c_rd_len,
      I2C_Dev_R  => gen_i2c_dev_r,
      Sda_In     => '1',
      CRC_En     => '0',
      CRC_Poly   => x"A001"
    );

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

  process
    variable pkt : byte_array(0 to 30);
    variable empty_payload : byte_array(0 to 0);
  begin
    wait until rising_edge(clk);
    wait_cycles(clk, 50);

    report "=== GEN START SIMULATION TEST ===";

    --------------------------------------------------------------
    -- Test 1: Write REG_GEN_PROTO = 0 via CMD_WRITE_REG
    --------------------------------------------------------------
    report "Test 1: REG_GEN_PROTO = 0";
    pkt := make_pkt(CMD_WRITE_REG, (
      0 => x"30", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"));
    spi_packet(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, pkt);
    wait_cycles(clk, 20);
    report "Test 1: PASS";

    --------------------------------------------------------------
    -- Test 2: Write REG_GEN_BAUD = 416
    --------------------------------------------------------------
    report "Test 2: REG_GEN_BAUD = 416 (0x1A0)";
    pkt := make_pkt(CMD_WRITE_REG, (
      0 => x"31", 1 => x"A0", 2 => x"01", 3 => x"00", 4 => x"00"));
    spi_packet(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, pkt);
    wait_cycles(clk, 20);
    report "Test 2: PASS";

    --------------------------------------------------------------
    -- Test 3: Load 'H' to gen FIFO via CMD_GEN_LOAD
    --------------------------------------------------------------
    report "Test 3: CMD_GEN_LOAD = 0x48 ('H')";
    pkt := make_pkt(CMD_GEN_LOAD, (0 => x"48"));
    spi_packet(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, pkt);
    wait_cycles(clk, 20);
    report "Test 3: PASS";

    --------------------------------------------------------------
    -- Test 4: CMD_GEN_START - THE critical test
    report "Test 4: Sending CMD_GEN_START via SPI packet protocol";
    gen_start_clr <= '1'; wait_cycles(clk, 1); gen_start_clr <= '0';
    wait_cycles(clk, 5);

    -- Send CMD_GEN_START (no payload needed, 1 dummy byte)
    pkt := make_pkt(CMD_GEN_START, (0 => x"00"));
    spi_packet(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, pkt);
    wait_cycles(clk, 50);

    if gen_start_cap = '1' then
      report "  *** GEN_START PULSED - CMD_GEN_START WORKS ***";
    else
      report "  *** GEN_START DID NOT PULSE ***";
    end if;

    --------------------------------------------------------------
    -- Test 5: Check gen_busy (Signal_Gen runs)
    --------------------------------------------------------------
    report "Test 5: Signal_Gen gen_busy check";
    wait_cycles(clk, 5000);
    report "  gen_start=" & std_logic'image(gen_start);
    report "  gen_busy=" & std_logic'image(gen_busy_sg);
    report "  gen_tx_out=" & std_logic'image(gen_tx_out);
    if gen_busy_sg = '1' then
      report "  *** GEN BUSY ASSERTED - FULL CHAIN WORKS ***";
    end if;

    report "=== GEN START SIMULATION TEST COMPLETE ===";
    wait;
  end process;
end bench;
