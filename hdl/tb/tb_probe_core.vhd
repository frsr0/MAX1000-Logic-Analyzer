-- Probe internal Core signals using VHDL-2008 external names
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_probe_core is
  generic (SPI_HALF : time := 50 ns);
end tb_probe_core;

architecture bench of tb_probe_core is
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
  signal dummy2 : natural range 0 to 31;

  -- Probe signals from hierarchy
  signal probe_run      : std_logic;
  signal probe_full     : std_logic;
  signal probe_run_ols  : std_logic;

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
      Gen_SCL_Pin => dummy2);

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  -- Probes using external names
  -- GHDL 6.0.0 crashes (NULL access, exit 255) elaborating external names
  -- into OLS_Interface internals from this hierarchy, so the diagnostic
  -- probes below are replaced with tie-offs. The test scenario itself (SPI
  -- register writes + ARM capture through the real OLS_Interface) is
  -- unchanged; only the passive Run/Full/Run_OLS change reports are dropped.
  probe_run     <= '0';
  probe_full    <= '0';
  probe_run_ols <= '0';

  monitor : process(clk)
    variable last_run : std_logic := '0';
    variable last_full : std_logic := '0';
    variable last_ols : std_logic := '0';
  begin
    if rising_edge(clk) then
      if probe_run /= last_run then
        report "Run changed to " & std_logic'image(probe_run)
          & " at " & time'image(now);
        last_run := probe_run;
      end if;
      if probe_full /= last_full then
        report "Full changed to " & std_logic'image(probe_full)
          & " at " & time'image(now);
        last_full := probe_full;
      end if;
      if probe_run_ols /= last_ols then
        report "Run_OLS changed to " & std_logic'image(probe_run_ols)
          & " at " & time'image(now);
        last_ols := probe_run_ols;
      end if;
    end if;
  end process;

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable empty : byte_array(0 to 0);
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);

    function flatten(b : byte_array; n : natural) return std_logic_vector is
      variable r : std_logic_vector(n*8-1 downto 0);
    begin
      for i in 0 to n-1 loop r(i*8+7 downto i*8) := b(b'low + i); end loop;
      return r;
    end function;

    -- GHDL 6.0.0 does not support VHDL-2008 sequential declare blocks, so the
    -- frame/read-back arrays are procedure-level variables and flatten is a
    -- process-level function (below stim's declarations).
    procedure wreg(reg : std_logic_vector(7 downto 0); value : integer) is
      variable tx : byte_array(0 to 300);
      variable rx : byte_array(0 to 300);
      variable dtx : byte_array(0 to 39);
      variable drx : byte_array(0 to 39);
      variable len_v : std_logic_vector(15 downto 0);
      variable crc_v : std_logic_vector(15 downto 0);
      variable crc_data : std_logic_vector((4+5)*8-1 downto 0);
    begin
      v := std_logic_vector(to_unsigned(value, 32));
      pld(0) := reg;
      pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
      pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
      -- send + read response
      tx(0) := x"55"; tx(1) := x"AA"; tx(2) := CMD_WRITE_REG; tx(3) := x"00";
      len_v := std_logic_vector(to_unsigned(5, 16));
      tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
      for i in 0 to 4 loop tx(6+i) := pld(i); end loop;
      crc_data := flatten(tx(2 to 10), 4+5);
      crc_v := crc16(crc_data);
      tx(11) := crc_v(7 downto 0); tx(12) := crc_v(15 downto 8);
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, tx(0 to 12), rx(0 to 12));
      wait for 6 us;
      for i in 0 to 39 loop dtx(i) := x"FF"; end loop;
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, dtx, drx);
    end procedure;

    procedure arm(variable st : out std_logic_vector(7 downto 0)) is
      variable tx : byte_array(0 to 300);
      variable rx : byte_array(0 to 300);
      variable dtx : byte_array(0 to 39);
      variable drx : byte_array(0 to 39);
      variable len_v : std_logic_vector(15 downto 0);
      variable crc_v : std_logic_vector(15 downto 0);
      variable i : natural;
    begin
      tx(0) := x"55"; tx(1) := x"AA"; tx(2) := CMD_ARM_CAPTURE; tx(3) := x"00";
      len_v := std_logic_vector(to_unsigned(0, 16));
      tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
      -- CRC over cmd(0x10) + seq(0x00) + len(0x0000)
      crc_v := crc16(std_logic_vector(to_unsigned(16#10#, 8)), x"FFFF");
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      tx(6) := crc_v(7 downto 0); tx(7) := crc_v(15 downto 8);
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, tx(0 to 7), rx(0 to 7));
      wait for 6 us;
      for i in 0 to 39 loop dtx(i) := x"FF"; end loop;
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, dtx, drx);
      st := x"FF";
      for i in 0 to 33 loop
        if drx(i) = x"AA" and drx(i+1) = x"55" then
          st := drx(i+2);
        end if;
      end loop;
    end procedure;

    procedure get_status(variable st : out std_logic_vector(7 downto 0)) is
      variable tx : byte_array(0 to 300);
      variable rx : byte_array(0 to 300);
      variable dtx : byte_array(0 to 39);
      variable drx : byte_array(0 to 39);
      variable len_v : std_logic_vector(15 downto 0);
      variable crc_v : std_logic_vector(15 downto 0);
      variable i : natural;
    begin
      tx(0) := x"55"; tx(1) := x"AA"; tx(2) := CMD_GET_STATUS; tx(3) := x"00";
      len_v := std_logic_vector(to_unsigned(0, 16));
      tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
      -- CRC over cmd(0x02) + seq(0x00) + len(0x0000)
      crc_v := crc16(std_logic_vector(to_unsigned(16#02#, 8)), x"FFFF");
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      crc_v := crc16(std_logic_vector(to_unsigned(0, 8)), crc_v);
      tx(6) := crc_v(7 downto 0); tx(7) := crc_v(15 downto 8);
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, tx(0 to 7), rx(0 to 7));
      wait for 6 us;
      for i in 0 to 39 loop dtx(i) := x"FF"; end loop;
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, dtx, drx);
      st := x"FF";
      for i in 0 to 33 loop
        if drx(i) = x"AA" and drx(i+1) = x"55" then
          st := drx(i+2);
        end if;
      end loop;
    end procedure;

  begin
    wait for 30 us;
    report "=== PROBE CORE TEST ===";

    wreg(REG_FLAGS, 0);
    wreg(REG_FAST_MODE, 1);
    wreg(REG_DIVIDER, 0);
    wreg(REG_SAMPLE_COUNT, 64);
    wreg(REG_DELAY_COUNT, 64);
    wreg(REG_TRIGGER_MASK, 0);
    wreg(REG_TRIGGER_VALUE, 0);
    wreg(REG_CONT_MODE, 0);
    wreg(REG_IFACE_MODE, 1);
    wait for 10 us;

    report "Arming...";
    arm(st);
    report "After arm status=" & to_hstring(st);
    check(st = ST_CAPTURE_ARMED,
          "ARM response must be ST_CAPTURE_ARMED, got " & to_hstring(st));

    wait for 200 us;

    get_status(st);
    report "Status after wait=" & to_hstring(st);
    check(st = ST_CAPTURE_DONE,
          "64-sample fast capture must complete to ST_CAPTURE_DONE, got " & to_hstring(st));

    report "Test complete";
    std.env.finish;
  end process;
end bench;
