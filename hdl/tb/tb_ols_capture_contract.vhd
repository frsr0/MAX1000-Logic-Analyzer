-- Capture contract regression tests for OLS_Interface:
--   * DONE is sticky until ACK, abort, or next arm.
--   * Abort suppresses stale Full from re-latching DONE.
--   * capture_seq increments on every arm.
--   * Mixed/digital mode state is fully rewritten on each arm sequence.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_ols_capture_contract is
  generic (
    CLK_FREQ : natural := 96000000;
    SPI_HALF : time    := 100 ns
  );
end tb_ols_capture_contract;

architecture bench of tb_ols_capture_contract is
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

  procedure pkt_read_rsp(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    variable status : out std_logic_vector(7 downto 0);
    variable payload : out byte_array; variable pay_len : out natural) is
    variable tx : byte_array(0 to 63);
    variable rx : byte_array(0 to 63);
    variable plen_v : natural;
    variable found : boolean := false;
  begin
    status := x"FF"; pay_len := 0;
    for i in tx'range loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx, rx);
    for i in 0 to 57 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        status := rx(i+2);
        plen_v := to_integer(unsigned(rx(i+5))) * 256
                + to_integer(unsigned(rx(i+4)));
        assert plen_v <= payload'length
          report "response payload exceeds capture-contract buffer" severity failure;
        for k in 0 to plen_v-1 loop
          payload(payload'low + k) := rx(i+6+k);
        end loop;
        pay_len := plen_v;
        found := true;
      end if;
    end loop;
    assert found report "framed SPI response not found" severity failure;
  end procedure;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable status : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 31);
    variable pay_len : natural;
  begin
    pkt_issue(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 8 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, status, pay, pay_len);
  end procedure;

  procedure get_status(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant empty : in byte_array;
    variable state : out std_logic_vector(7 downto 0);
    variable seq : out unsigned(31 downto 0);
    variable done : out std_logic) is
    variable pay : byte_array(0 to 31);
    variable pay_len : natural;
    variable seq_bits : std_logic_vector(31 downto 0);
  begin
    pkt_issue(cs_n, sck_o, mosi, miso, CMD_GET_STATUS, empty, 0);
    wait for 8 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, state, pay, pay_len);
    assert pay_len >= 24
      report "GET_STATUS payload shorter than the documented 24-byte contract"
      severity failure;
    seq_bits(7 downto 0) := pay(3);
    seq_bits(15 downto 8) := pay(4);
    seq_bits(23 downto 16) := pay(5);
    seq_bits(31 downto 24) := pay(6);
    seq := unsigned(seq_bits);
    done := pay(23)(0);
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
    variable ack_zero : byte_array(0 to 3);
    variable seq0, seq_v : unsigned(31 downto 0);
    variable done_v : std_logic;
    variable state_v : std_logic_vector(7 downto 0);
  begin
    ack_zero := (others => x"00");
    wait_cycles(clk, 100);

    report "=== OLS capture contract tests ===";
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq0, done_v);
    check(done_v = '0', "public DONE status starts clear");

    report "Test 1: arm increments capture_seq and clears DONE";
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(seq_v = seq0 + 1, "public capture_seq increments on arm");
    seq0 := seq_v;
    check(done_v = '0', "public DONE clear after arm");

    report "Test 2: Full latches DONE and DONE is sticky";
    full <= '1';
    wait_cycles(clk, 10);
    full <= '0';
    wait_cycles(clk, 40);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(state_v = ST_CAPTURE_DONE, "GET_STATUS reports capture DONE");
    check(done_v = '1', "DONE latched from Full");
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(done_v = '1', "DONE remains latched across repeated status reads");

    report "Test 3: ACK clears DONE";
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ACK_CAPTURE_DONE, ack_zero, 4);
    wait_cycles(clk, 40);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(done_v = '0', "DONE clears on ACK wildcard");

    report "Test 4: abort clears DONE and suppresses stale Full";
    full <= '1';
    wait_cycles(clk, 10);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(done_v = '1', "DONE re-latched before abort");
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
    wait_cycles(clk, 80);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(done_v = '0', "DONE stays clear after abort even while Full is stale");
    full <= '0';
    wait_cycles(clk, 20);

    report "Test 5: next arm clears abort suppression and can latch DONE again";
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(seq_v = seq0 + 1, "capture_seq increments on re-arm");
    seq0 := seq_v;
    check(done_v = '0', "re-arm clears abort suppression and DONE");
    full <= '1';
    wait_cycles(clk, 10);
    full <= '0';
    wait_cycles(clk, 20);
    get_status(spi_cs, spi_sck, spi_mosi, spi_miso, empty, state_v, seq_v, done_v);
    check(done_v = '1', "DONE can latch after next arm");
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ACK_CAPTURE_DONE, ack_zero, 4);
    wait_cycles(clk, 40);

    report "Test 6: mixed -> digital -> mixed mode writes complete state";
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FLAGS, 16#08#);
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    check(analog_enable = '1', "mixed arm sets analog_enable");
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
    wait_cycles(clk, 40);

    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    check(analog_enable = '0', "digital arm clears analog_enable");
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
    wait_cycles(clk, 40);

    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FLAGS, 16#08#);
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    check(analog_enable = '1', "second mixed arm restores analog_enable");

    report "Test 7: analog-only mode sets both analog flags";
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FLAGS, 16#18#);
    pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0);
    wait_cycles(clk, 40);
    check(analog_enable = '1', "analog-only arm sets analog_enable");
    check(analog_only = '1', "analog-only arm sets analog_only");

    report "=== ALL OLS CAPTURE CONTRACT TESTS PASSED ===";
    stop;
  end process;

end bench;
