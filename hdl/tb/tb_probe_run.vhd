-- Quick probe: does Run assert in the Core after arm?
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_probe_run is
  generic (SPI_HALF : time := 50 ns);
end tb_probe_run;

architecture bench of tb_probe_run is
  constant CLK_PERIOD : time := 1 sec / 100000000;
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal spi_cs   : std_logic := '1';
  signal sck      : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;
  signal inputs_fast : std_logic_vector(15 downto 0) := (others => '0');
  signal sdram_addr  : std_logic_vector(11 downto 0);
  signal sdram_ba    : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_clk   : std_logic;
  signal dummy0 : std_logic;
  signal dummy1 : natural range 0 to 31;

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
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd; tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  procedure pkt_cmd(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural;
    variable status : out std_logic_vector(7 downto 0)) is
    variable pay : byte_array(0 to 63);
    variable pl  : natural;
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 6 us;
    -- Read 40 bytes to find response
    pay := (others => x"00");
    pl := 0;
    status := x"FF";
    -- Simple read
    declare
      variable tx : byte_array(0 to 39);
      variable rx : byte_array(0 to 39);
    begin
      for i in 0 to 39 loop tx(i) := x"FF"; end loop;
      spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx, rx);
      for i in 0 to 33 loop
        if rx(i) = x"AA" and rx(i+1) = x"55" then
          status := rx(i+2);
        end if;
      end loop;
    end;
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0);
    constant value : in integer) is
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);
    variable st : std_logic_vector(7 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg;
    pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_WRITE_REG, pld, 5, st);
  end procedure;

begin
  gen_clk(clk, 5 ns);
  process begin
    fast_clk <= '1'; wait for 2.5 ns;
    fast_clk <= '0'; wait for 2.5 ns;
  end process;

  process(fast_clk) begin
    if rising_edge(fast_clk) then
      inputs_fast <= std_logic_vector(unsigned(inputs_fast) + 1);
    end if;
  end process;

  DUT : entity work.OLS_Logic_Analyzer
    generic map (Sim => true, FAST_SPEED => true,
      Max_Samples => 16384, Channels => 16,
      CLK_Frequency => 100_000_000, SDRAM_CLK_HZ => 166_666_667,
      SAMPLE_CLK_HZ => 200_000_000)
    port map (
      CLK => clk, SDRAM_CLK_IN => '0', FAST_CLK => fast_clk,
      Inputs_Sys => inputs_fast, Inputs_Fast => inputs_fast,
      SPI_CS => spi_cs, SPI_SCK => sck,
      SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Interface_Mode => dummy0,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba,
      sdram_cas_n => sdram_cas_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_cke => sdram_cke,
      sdram_cs_n => sdram_cs_n, sdram_clk => sdram_clk,
      Gen_Busy => '0', Gen_Fifo_Count => x"00",
      Gen_Start_Ack => '0', Gen_Start_Reject => '0',
      Gen_Done_Pulse => '0',
      Armed => dummy0, Fast_Mode => dummy0,
      Analog_Frame_Data => (others => '0'),
      Gen_Proto => dummy0, Gen_TX_Pin => dummy1,
      Gen_SCL_Pin => dummy1);

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable empty : byte_array(0 to 0);
  begin
    wait for 30 us;
    report "=== PROBE RUN TEST ===";

    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 64);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 64);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_IFACE_MODE, 1);
    wait for 10 us;

    report "Arming...";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    report "After arm status=" & to_hstring(st);

    wait for 100 us;

    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
    report "Status after wait=" & to_hstring(st);

    -- Try another arm + longer wait
    report "Re-arming...";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    report "After arm2 status=" & to_hstring(st);
    wait for 500 us;

    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
    report "Status after 500us=" & to_hstring(st);

    std.env.finish;
  end process;
end bench;
