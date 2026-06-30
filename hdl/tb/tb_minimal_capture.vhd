-- Minimal test: OLS_Interface drives the real FLA, capture completes
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_minimal_capture is
  generic (SPI_HALF : time := 50 ns);
end tb_minimal_capture;

architecture bench of tb_minimal_capture is
  constant CLK_PERIOD : time := 1 sec / 100000000;

  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal spi_cs   : std_logic := '1';
  signal sck      : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;
  signal iface_mode : std_logic;

  signal inputs   : std_logic_vector(31 downto 0) := (others => '0');
  signal addr_s   : natural := 0;
  signal samples  : natural range 1 to 25000;
  signal start_off : natural range 0 to 25000;
  signal run_s    : std_logic;
  signal full_s   : std_logic;
  signal addr_s   : natural range 0 to 25000 := 0;
  signal outputs  : std_logic_vector(31 downto 0) := (others => '0');

  signal armed    : std_logic;
  signal fast_mode : std_logic;
  signal continuous_mode : std_logic;

  signal blk_req_tog : std_logic := '0';
  signal blk_base    : natural range 0 to 25000 := 0;
  signal blk_count   : natural range 0 to 25000 := 0;
  signal auto_renew  : std_logic := '0';
  signal rd_fifo_q   : std_logic_vector(15 downto 0);
  signal rd_fifo_empty : std_logic;
  signal rd_fifo_rdreq : std_logic := '0';
  signal producer_index_s : std_logic_vector(31 downto 0);

  -- SDRAM pins
  signal sdram_addr  : std_logic_vector(11 downto 0);
  signal sdram_ba    : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_clk_s : std_logic;

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

  procedure pkt_read_rsp(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant rlen : in natural;
    variable status : out std_logic_vector(7 downto 0);
    variable payload : out byte_array;
    variable pay_len : out natural) is
    variable tx : byte_array(0 to 1199);
    variable rx : byte_array(0 to 1199);
    variable plen_v : natural;
    variable found : boolean := false;
  begin
    status := x"FF"; pay_len := 0;
    for i in 0 to rlen-1 loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to rlen-1), rx(0 to rlen-1));
    for i in 0 to rlen-7 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        status := rx(i+2);
        plen_v := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
        if plen_v > 1100 then plen_v := 0; end if;
        for k in 0 to plen_v-1 loop
          if i+6+k <= rlen-1 then
            payload(payload'low + k) := rx(i+6+k);
          end if;
        end loop;
        pay_len := plen_v;
        found := true;
      end if;
    end loop;
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
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 40, status, pay, pl);
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
    pkt_cmd(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5, st);
  end procedure;

begin
  gen_clk(clk, 5 ns);
  fastclk_gen : process
  begin
    fast_clk <= '1'; wait for 2.5 ns;
    fast_clk <= '0'; wait for 2.5 ns;
  end process;

  -- Counter input
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      inputs(15 downto 0) <= std_logic_vector(unsigned(inputs(15 downto 0)) + 1);
    end if;
  end process;

  OLS : entity work.OLS_Interface
    generic map (CLK_Frequency => 100_000_000, Max_Samples => 25000)
    port map (
      CLK => clk, FAST_CLK => fast_clk,
      SPI_CS => spi_cs, SPI_SCK => sck,
      SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Interface_Mode => iface_mode, Inputs => inputs,
      Rate_Div => rate_div, Samples => samples,
      Start_Offset => start_off, Run => run_s,
      Full => full_s, Address => addr_s, Outputs => outputs,
      Gen_Busy => '0', Armed => armed, Fast_Mode => fast_mode,
      Continuous_Mode => continuous_mode,
      Blk_Rd_Req_Tog => blk_req_tog, Blk_Rd_Base => blk_base,
      Blk_Rd_Count => blk_count, Auto_Renew => auto_renew,
      Rd_Fifo_Q => rd_fifo_q, Rd_Fifo_Empty => rd_fifo_empty,
      Rd_Fifo_RdReq => rd_fifo_rdreq,
      Producer_Index => producer_index_s, Oldest_Index => open,
      Newest_Index => open, Overrun_Count => open);

  FLA : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 25000, Channels => 16,
      Sim => true, FAST_SPEED => true,
      CLK_Frequency => 100000000, SDRAM_CLK_HZ => 166666667,
      SAMPLE_CLK_HZ => 200000000)
    port map (
      CLK => clk, SDRAM_CLK_IN => '0', FAST_CLK => fast_clk,
      Rate_Div => rate_div, Samples => samples,
      Start_Offset => start_off, Run => run_s, Full => full_s,
      Inputs => inputs(15 downto 0), Address => addr_s,
      Outputs => outputs(15 downto 0),
      sdram_addr => sdram_addr, sdram_ba => sdram_ba,
      sdram_cas_n => sdram_cas_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_cke => sdram_cke,
      sdram_cs_n => sdram_cs_n, sdram_clk => sdram_clk_s,
      Armed => armed, Fast_Mode => fast_mode,
      Continuous_Mode => continuous_mode,
      Blk_Rd_Req_Tog => blk_req_tog, Blk_Rd_Base => blk_base,
      Blk_Rd_Count => blk_count, Auto_Renew => auto_renew,
      Rd_Fifo_Q => rd_fifo_q, Rd_Fifo_Empty => rd_fifo_empty,
      Rd_Fifo_RdReq => rd_fifo_rdreq,
      Producer_Index => producer_index_s, Oldest_Index => open,
      Newest_Index => open, Overrun_Count => open);

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (clk => sdram_clk_s, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 63);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable deadline : natural;
  begin
    wait for 30 us;

    report "=== MINIMAL CAPTURE TEST ===";

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

    deadline := 0;
    loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      exit when st = ST_CAPTURE_DONE or deadline > 100;
      deadline := deadline + 1;
      wait for 10 us;
    end loop;

    if st /= ST_CAPTURE_DONE then
      report "CAPTURE NOT DONE after " & integer'image(deadline)
             & " polls, status=" & to_hstring(st) severity failure;
    end if;
    report "=== CAPTURE DONE ===";
    std.env.finish;
  end process;
end bench;
