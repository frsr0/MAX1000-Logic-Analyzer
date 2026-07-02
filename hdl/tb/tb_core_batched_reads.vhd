-- Full-core reproduction of the during-capture batched block-read stall
-- (2026-07-02 HW: first 1-2 CS-held CMD_READ_CAPTURE blocks succeed during
-- continuous capture, every later one times out into the WAIT_BLOCK watchdog).
-- OLS_Logic_Analyzer core (OLS_Interface + SPI slave + real FLA + real SDRAM
-- controller) + sdram_pin_model, 30 MHz SCK, production slot spacing.
--
-- The capture input is a free-running 16-bit counter on FAST_CLK, so sample k
-- has value (k*div_total + offset) mod 65536 — block payloads are checked for
-- the exact per-sample ramp (catches stale/wrong-address data, not just
-- framing).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_core_batched_reads is
  generic (
    SPI_HALF  : time := 16.67 ns;   -- 30 MHz SCK like hardware
    RATE_DIV  : natural := 20;      -- 200 MHz / 20 = 10 MHz sample rate
    N_BLOCKS  : natural := 6;
    SLOT_PAD  : natural := 1264     -- 12-byte request + 208 gap + 1056 rsp pad
  );
end tb_core_batched_reads;

architecture bench of tb_core_batched_reads is
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal spi_cs   : std_logic := '1';
  signal sck      : std_logic := '0';
  signal spi_mosi : std_logic := '1';
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
  signal iface_mode  : std_logic;
  signal armed_fast  : std_logic;
  signal fast_mode_fast : std_logic;

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop r(i*8+7 downto i*8) := b(b'low + i); end loop;
    return r;
  end function;

  procedure build_req(
    variable tx  : inout byte_array;
    constant off : in natural;
    constant cmd : in std_logic_vector(7 downto 0);
    constant seq : in natural;
    constant payload : in byte_array;
    constant plen : in natural) is
    variable len_v, crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
    variable hdr : byte_array(0 to 60);
  begin
    hdr(0) := x"55"; hdr(1) := x"AA";
    hdr(2) := cmd;
    hdr(3) := std_logic_vector(to_unsigned(seq, 8));
    len_v := std_logic_vector(to_unsigned(plen, 16));
    hdr(4) := len_v(7 downto 0); hdr(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop hdr(6+i) := payload(i); end loop;
    crc_data := flatten(hdr(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    hdr(6+plen) := crc_v(7 downto 0); hdr(7+plen) := crc_v(15 downto 8);
    for i in 0 to 7+plen loop tx(off+i) := hdr(i); end loop;
  end procedure;

begin
  gen_clk(clk, 5 ns);

  fastclk_gen : process
  begin
    fast_clk <= '1'; wait for 2.5 ns;
    fast_clk <= '0'; wait for 2.5 ns;
  end process;

  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      inputs_fast <= std_logic_vector(unsigned(inputs_fast) + 1);
    end if;
  end process;

  DUT : entity work.OLS_Logic_Analyzer
    generic map (
      Sim => true, FAST_SPEED => true,
      Max_Samples => 16384, Channels => 16,
      CLK_Frequency => 100_000_000,
      SDRAM_CLK_HZ => 166_666_667,
      SAMPLE_CLK_HZ => 200_000_000)
    port map (
      CLK => clk, SDRAM_CLK_IN => '0', FAST_CLK => fast_clk,
      Inputs_Sys => inputs_fast, Inputs_Fast => inputs_fast,
      SPI_CS => spi_cs, SPI_SCK => sck,
      SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Interface_Mode => iface_mode,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba,
      sdram_cas_n => sdram_cas_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_cke => sdram_cke,
      sdram_cs_n => sdram_cs_n, sdram_clk => sdram_clk,
      Gen_Busy => '0', Gen_Fifo_Count => x"00",
      Gen_Start_Ack => '0', Gen_Start_Reject => '0',
      Gen_Done_Pulse => '0',
      Armed => armed_fast, Fast_Mode => fast_mode_fast,
      Analog_Frame_Data => (others => '0'),
      Analog_Frame_Len => 1,
      Analog_Stream_Mode => '0', Analog_Frame_Toggle => '0',
      Gen_Proto => open, Gen_TX_Pin => open,
      Gen_SCL_Pin => open,
      Gen_Load_Byte => open, Gen_Load_We => open,
      Gen_Start => open, Gen_Baud_Div => open,
      Gen_Clear => open, Gen_I2C_Rd_Len => open,
      Gen_I2C_Dev_R => open, Gen_I2C_Test => open,
      Gen_SPI_Test => open, Gen_Repeat => open,
      Gen_RS485_Pair => open, Status => open,
      Narrow_Enable => open, Narrow_Channel => open,
      Analog_Enable => open, Analog_Only => open,
      Analog_Profile => open, Analog_Channel => open,
      Continuous_Mode => open,
      Pin_Map_Write => open, Pin_Map_Channel => open,
      Pin_Map_Pin => open, Debug_Ch0_Enable => open,
      Debug_Ch0_Period => open, Debug_Ch0_Duty => open,
      Gen_Capture_Active => open,
      Pump_Valid_Cycles => open, Pump_Ready_Cycles => open,
      Pump_Accept_Cycles => open, Pump_Stall_Cycles => open,
      Pump_NoData_Cycles => open, Pump_Overflow_Count => open);

  SDRAM : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => false)
    port map (
      clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm,
      dq => sdram_dq);

  stim : process
    constant BURST_LEN : natural := 64 + N_BLOCKS * SLOT_PAD;
    variable tx : byte_array(0 to BURST_LEN - 1) := (others => x"FF");
    variable rx : byte_array(0 to BURST_LEN - 1);
    variable pl : byte_array(0 to 7);
    variable off : natural := 0;
    variable n_ok, n_bad, n_dup, n_wrong : natural := 0;
    variable plen_v, rsp_seq : natural;
    variable seen : std_logic_vector(0 to 255) := (others => '0');
    variable base_expect : natural;
    variable w_prev, w_cur : integer;
    variable ramp_errs : natural;
    variable t2 : byte_array(0 to 63);
    variable r2 : byte_array(0 to 63);

    procedure wreg(constant regaddr : std_logic_vector(7 downto 0);
                   constant value : natural) is
      variable p : byte_array(0 to 7);
      variable v : std_logic_vector(31 downto 0);
    begin
      v := std_logic_vector(to_unsigned(value, 32));
      p(0) := regaddr;
      p(1) := v(7 downto 0); p(2) := v(15 downto 8);
      p(3) := v(23 downto 16); p(4) := v(31 downto 24);
      t2 := (others => x"FF");
      build_req(t2, 0, x"20", 1, p, 5);
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, t2(0 to 60), r2(0 to 60));
      wait for 2 us;
    end procedure;

    procedure do_arm is
      variable p : byte_array(0 to 0);
    begin
      t2 := (others => x"FF");
      build_req(t2, 0, x"10", 2, p, 0);
      spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF, t2(0 to 60), r2(0 to 60));
      wait for 2 us;
    end procedure;

  begin
    wait for 4 us;
    report "=== CORE BATCHED DURING-CAPTURE READ TEST ===";
    wreg(x"20", 16#02#);       -- REG_FLAGS: continuous bit
    wreg(x"21", 1);            -- REG_FAST_MODE
    wreg(x"00", RATE_DIV - 1); -- REG_DIVIDER (down-counter reload)
    wreg(x"01", 16000);        -- REG_SAMPLE_COUNT
    wreg(x"02", 16000);        -- REG_DELAY_COUNT
    wreg(x"22", 1);            -- REG_CONT_MODE = 1
    do_arm;

    -- Let the ring fill: 4000 samples at 10 MS/s = 400 us.
    wait for 450 us;

    -- CS-held burst: N_BLOCKS CMD_READ_CAPTURE at production slot spacing.
    off := 0;
    for i in 0 to N_BLOCKS - 1 loop
      base_expect := 512 + i * 512;   -- sample index (byte addr = *2)
      pl(0) := std_logic_vector(to_unsigned((base_expect * 2) mod 256, 8));
      pl(1) := std_logic_vector(to_unsigned(((base_expect * 2) / 256) mod 256, 8));
      pl(2) := x"00"; pl(3) := x"00";
      build_req(tx, off, x"12", 16 + i, pl, 4);
      off := off + SLOT_PAD;
    end loop;

    spi_xfer(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
             tx(0 to off + 40), rx(0 to off + 40));

    -- scan responses
    for i in 0 to off + 20 loop
      if rx(i) = x"AA" and rx(i+1) = x"55" then
        plen_v := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
        rsp_seq := to_integer(unsigned(rx(i+3)));
        if rsp_seq >= 16 and rsp_seq < 16 + N_BLOCKS and plen_v <= 1024 then
          if rx(i+2) = x"00" and plen_v = 1024 then
            if seen(rsp_seq) = '1' then
              n_dup := n_dup + 1;
            end if;
            seen(rsp_seq) := '1';
            -- ramp check: consecutive samples differ by RATE_DIV
            ramp_errs := 0;
            w_prev := -1;
            for k in 0 to 511 loop
              w_cur := to_integer(unsigned(rx(i + 7 + 2*k))) * 256
                       + to_integer(unsigned(rx(i + 6 + 2*k)));
              if w_prev >= 0 and ((w_prev + RATE_DIV) mod 65536) /= w_cur then
                ramp_errs := ramp_errs + 1;
              end if;
              w_prev := w_cur;
            end loop;
            if ramp_errs <= 1 then
              n_ok := n_ok + 1;
            else
              n_wrong := n_wrong + 1;
              report "block seq " & integer'image(rsp_seq) & ": "
                     & integer'image(ramp_errs) & " ramp errors";
            end if;
          else
            n_bad := n_bad + 1;
            report "NON-OK response seq " & integer'image(rsp_seq)
                   & " status=0x" & to_hstring(rx(i+2))
                   & " len=" & integer'image(plen_v)
                   & " at byte " & integer'image(i);
          end if;
        end if;
      end if;
    end loop;

    report "RESULT: ok=" & integer'image(n_ok)
           & " ramp-bad=" & integer'image(n_wrong)
           & " non-ok=" & integer'image(n_bad)
           & " duplicates=" & integer'image(n_dup)
           & " of " & integer'image(N_BLOCKS) & " requests";
    check(n_ok = N_BLOCKS, "all during-capture block reads return clean ramp data");
    check(n_bad = 0, "no watchdog/error responses");
    check(n_dup = 0, "no duplicate responses");
    report "=== TB PASSED ===";
    finish;
  end process;
end bench;
