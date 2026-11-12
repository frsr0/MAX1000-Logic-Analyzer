-- Edge-trigger arm regression for OLS_Interface:
--   * A rising/falling edge trigger armed while channel 0 sits at a constant
--     level must not fire (inputs_prev must not be stale from an earlier arm).
--   * A genuine edge of the configured polarity must still fire.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_edge_trigger_arm is
  generic (
    CLK_FREQ : natural := 96000000;
    SPI_HALF : time    := 100 ns
  );
end tb_edge_trigger_arm;

architecture bench of tb_edge_trigger_arm is
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
  signal gen_baud_div  : std_logic_vector(23 downto 0);
  signal gen_busy      : std_logic := '0';
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 31;
  signal gen_scl_pin   : natural range 0 to 31;
  signal gen_i2c_rd_len : natural range 0 to 255;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0);
  signal gen_i2c_test   : std_logic;
  signal gen_spi_test   : std_logic;
  signal armed        : std_logic;
  signal fast_mode    : std_logic;
  signal continuous_mode : std_logic;
  signal analog_enable : std_logic;
  signal analog_only   : std_logic;
  signal buffer_full  : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack   : std_logic_vector(2 downto 0);

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  procedure pkt_issue(
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

  -- Consume the framed response so the next command starts clean.
  procedure pkt_read_rsp(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic) is
    variable tx : byte_array(0 to 63);
    variable rx : byte_array(0 to 63);
  begin
    for i in tx'range loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx, rx);
  end procedure;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
  begin
    pkt_issue(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 8 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso);
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0);
    constant v : in std_logic_vector(31 downto 0)) is
    variable pld : byte_array(0 to 4);
  begin
    pld(0) := reg;
    pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_send(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5);
  end procedure;

begin
  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;

  DUT : entity work.OLS_Interface
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
      Armed => armed, Fast_Mode => fast_mode, Continuous_Mode => continuous_mode,
      Analog_Enable => analog_enable, Analog_Only => analog_only,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack
    );

  process
    variable empty : byte_array(0 to 0);

    -- Run must stay low for n clocks (flags any glitch, not just the end state).
    procedure expect_run_low(constant n : natural; constant msg : string) is
    begin
      for i in 1 to n loop
        wait until rising_edge(clk);
        check(run = '0', msg);
      end loop;
    end procedure;

    procedure expect_run_high(constant n : natural; constant msg : string) is
      variable seen : boolean := false;
    begin
      for i in 1 to n loop
        wait until rising_edge(clk);
        if run = '1' then seen := true; end if;
      end loop;
      check(seen, msg);
    end procedure;

    -- One scenario. ch0 sits at prime_v while a level trigger that cannot
    -- match is armed (so inputs_prev records prime_v), is aborted, then ch0 is
    -- held at hold_v and the edge trigger is armed. No edge exists, so Run must
    -- stay low; a real edge afterwards must raise it.
    procedure scenario(constant rising : boolean; constant tag : string) is
      variable prime_v, hold_v : std_logic;
      variable mode_mask : std_logic_vector(31 downto 0);
      variable lvl_value : std_logic_vector(31 downto 0);
    begin
      if rising then
        prime_v := '0'; hold_v := '1';
        mode_mask := x"40000001";
        lvl_value := x"00000001";   -- wants ch0 high: never true while low
      else
        prime_v := '1'; hold_v := '0';
        mode_mask := x"80000001";
        lvl_value := x"00000000";   -- wants ch0 low: never true while high
      end if;

      -- (a) arm an unmatchable level trigger so inputs_prev records prime_v
      inputs(0) <= prime_v;
      wait_cycles(clk, 20);
      wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, lvl_value);
      wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, x"00000001");
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
      expect_run_low(200, tag & ": level trigger fired while ch0 at non-matching level");
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
      wait_cycles(clk, 100);

      -- (b) hold ch0 constant at the opposite level
      inputs(0) <= hold_v;
      wait_cycles(clk, 100);

      -- (c) edge trigger armed with ch0 constant
      wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, mode_mask);
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);

      -- (d) constant level: no edge, so Run must never rise
      expect_run_low(2000, tag & ": edge trigger fired on a constant level (stale inputs_prev)");

      -- (e) go to the prime level (wrong-polarity transition: must not fire),
      -- then back to hold_v (matching edge: must fire).
      inputs(0) <= prime_v;
      expect_run_low(50, tag & ": wrong-polarity transition fired the trigger");
      inputs(0) <= hold_v;
      expect_run_high(50, tag & ": genuine edge did not fire the trigger");

      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
      wait_cycles(clk, 100);
    end procedure;
  begin
    wait_cycles(clk, 100);
    report "=== Edge trigger arm tests ===";

    report "Scenario 1: rising edge, ch0 constant high after being low at last arm";
    scenario(true, "rising");

    report "Scenario 2: falling edge, ch0 constant low after being high at last arm";
    scenario(false, "falling");

    report "=== ALL EDGE TRIGGER ARM TESTS PASSED ===";
    stop;
  end process;

end bench;
