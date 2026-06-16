-- Generator full-chain integration test (OLS_Interface -> Signal_Gen -> UART).
--
-- Drives the generator entirely through the real framed SPI command protocol
-- (REG_GEN_* config, CMD_GEN_LOAD, CMD_GEN_START) and checks that Signal_Gen
-- asserts Busy and toggles its Tx_Out. The previous version forced the internal
-- disp_gen_start via an external name, which crashed GHDL and also double-drove
-- the OLS_Interface generator outputs; driving over SPI removes both problems.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_gen_full is
  generic (
    CLK_FREQ : natural := 96000000;
    SPI_HALF : time    := 100 ns
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
  signal rate_div  : natural range 1 to 500000000;
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
  signal analog_enable : std_logic;
  signal buffer_full  : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack   : std_logic_vector(2 downto 0);
  signal pin_map_write : std_logic;
  signal pin_map_ch    : natural range 0 to 15;
  signal pin_map_pin   : natural range 0 to 31;

  signal gen_tx_out  : std_logic;
  signal gen_scl_out : std_logic;

  signal gen_busy_cap : std_logic := '0';
  signal gen_tx_edges : natural := 0;

  -- Framed SPI packet send: 0x55 0xAA cmd seq len(2) payload crc16(2)
  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable tx : byte_array(0 to 300);
    variable rx : byte_array(0 to 300);
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA"; tx(2) := cmd; tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0); constant value : in integer) is
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg;
    pld(1) := v(7 downto 0);  pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_send(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5);
  end procedure;

begin
  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;

  DUT_IFACE : entity work.OLS_Interface
    generic map (CLK_Frequency => CLK_FREQ, Max_Samples => 25000)
    port map (
      CLK => clk, FAST_CLK => fast_clk,
      SPI_CS => spi_cs, SPI_SCK => spi_sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Interface_Mode => iface_mode, Inputs => inputs,
      Rate_Div => rate_div, Samples => samples, Start_Offset => start_off,
      Run => run, Full => full, Address => address, Outputs => outputs,
      Gen_Load_Byte => gen_load_byte, Gen_Load_We => gen_load_we, Gen_Start => gen_start,
      Gen_Baud_Div => gen_baud_div, Gen_Busy => gen_busy, Gen_Proto => gen_proto,
      Gen_TX_Pin => gen_tx_pin, Gen_SCL_Pin => gen_scl_pin,
      Gen_I2C_Rd_Len => gen_i2c_rd_len, Gen_I2C_Dev_R => gen_i2c_dev_r,
      Gen_I2C_Test => gen_i2c_test, Gen_SPI_Test => gen_spi_test,
      Armed => armed, Fast_Mode => fast_mode, Continuous_Mode => cont_mode,
      Analog_Enable => analog_enable, Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      Pin_Map_Write => pin_map_write, Pin_Map_Channel => pin_map_ch, Pin_Map_Pin => pin_map_pin
    );

  DUT_GEN : entity work.Signal_Gen
    generic map (FIFO_DEPTH => 256)
    port map (
      CLK => clk, Load_Byte => gen_load_byte, Load_We => gen_load_we, Start => gen_start,
      Baud_Div => gen_baud_div, Proto => gen_proto, SPI_Mode => gen_spi_test,
      Tx_Out => gen_tx_out, Scl_Out => gen_scl_out, Busy => gen_busy, Active => open,
      I2C_Rd_Len => gen_i2c_rd_len, I2C_Dev_R => gen_i2c_dev_r, Sda_In => '1',
      CRC_En => '0', CRC_Poly => x"A001"
    );

  -- Latch whether Signal_Gen ever asserted Busy
  process(clk)
  begin
    if rising_edge(clk) then
      if gen_busy = '1' then gen_busy_cap <= '1'; end if;
    end if;
  end process;

  -- Count Tx_Out transitions (UART line activity)
  process(clk)
    variable prev : std_logic := '1';
  begin
    if rising_edge(clk) then
      if gen_tx_out /= prev then
        gen_tx_edges <= gen_tx_edges + 1;
        prev := gen_tx_out;
      end if;
    end if;
  end process;

  process
    variable empty : byte_array(0 to 0);
  begin
    wait_cycles(clk, 100);
    report "=== GEN FULL CHAIN TEST (SPI-driven) ===";

    -- Configure the generator: UART protocol, a fast baud (small divider keeps
    -- the sim short), tx pin 3 / scl pin 1.
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_GEN_PROTO, 0);
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_GEN_BAUD, 8);
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_GEN_PINS, 16#0103#);
    wait_cycles(clk, 20);

    -- Load "Hello" through CMD_GEN_LOAD (one payload byte per packet).
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_LOAD, byte_array'(0 => x"48"), 1); -- 'H'
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_LOAD, byte_array'(0 => x"65"), 1); -- 'e'
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_LOAD, byte_array'(0 => x"6C"), 1); -- 'l'
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_LOAD, byte_array'(0 => x"6C"), 1); -- 'l'
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_LOAD, byte_array'(0 => x"6F"), 1); -- 'o'
    report "Loaded 5 bytes via CMD_GEN_LOAD";

    -- Start the generator.
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_GEN_START, empty, 0);
    report "Sent CMD_GEN_START";

    wait_until(clk, gen_busy, '1', 1 ms, "Signal_Gen should assert Busy after CMD_GEN_START");
    report "Signal_Gen busy";

    -- Let the 5-byte burst transmit (5 * 10 bits * 8-cycle baud ~ 400 cycles).
    wait_cycles(clk, 20000);

    check(gen_busy_cap = '1', "Signal_Gen Busy asserted (OLS->Gen chain works)");
    check(gen_tx_edges > 4, "Tx_Out toggled (UART output present), edges="
                            & integer'image(gen_tx_edges));
    report "gen_tx_edges=" & integer'image(gen_tx_edges)
         & " gen_busy(now)=" & std_logic'image(gen_busy);

    report "=== GEN FULL CHAIN TEST PASSED ===";
    wait;
  end process;
end bench;
