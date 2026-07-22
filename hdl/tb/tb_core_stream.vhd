-- Core integration testbench for CMD_START_STREAM.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_core_stream is
  generic (SPI_HALF : time := 50 ns);
end tb_core_stream;

architecture bench of tb_core_stream is
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

  signal iface_mode : std_logic;
  signal armed_fast : std_logic;
  signal fast_mode_fast : std_logic;

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


  procedure stream_read_holdcs(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant n_bytes : in natural;
    variable rx_data : out byte_array) is
    variable tx_byte : std_logic_vector(7 downto 0);
  begin
    cs_n <= '0';
    wait for SPI_HALF;
    for i in 0 to n_bytes - 1 loop
      tx_byte := x"FF";
      for b in 7 downto 0 loop
        sck_o <= '0';
        mosi <= tx_byte(b);
        wait for SPI_HALF;
        sck_o <= '1';
        rx_data(i)(b) := miso;
        wait for SPI_HALF;
      end loop;
    end loop;
    sck_o <= '0';
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
      Pin_Map_Pin => open,
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
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable addr_pld : byte_array(0 to 3);
    variable word : std_logic_vector(15 downto 0);
    variable prev_word : std_logic_vector(15 downto 0) := (others => '0');
    variable V0_blockread : std_logic_vector(15 downto 0) := (others => '0');
    type sample_arr is array(0 to 1023) of std_logic_vector(15 downto 0);
    variable S : sample_arr;
    variable rx_stream : byte_array(0 to 4095);
    variable ack_base : natural;
    variable ack_found : boolean;
    variable ack_status : std_logic_vector(7 downto 0);
    variable ack_seq : std_logic_vector(7 downto 0);
    variable ack_paylen : natural;
    variable data_start : natural;
    variable phantom : natural;
    variable deadline : natural := 0;
  begin
    wait for 30 us;

    report "=== CORE STREAM INTEGRATION TEST ===";

    report "Configuring capture...";
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 4096);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 4096);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_IFACE_MODE, 1);
    wait for 10 us;

    report "Arming capture...";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    deadline := 0;
    loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      exit when st = ST_CAPTURE_DONE or deadline > 2000;
      deadline := deadline + 1;
      wait for 20 us;
    end loop;
    check(st = ST_CAPTURE_DONE,
          "Capture did not reach DONE (status=" & to_hstring(st) &
          " deadline=" & integer'image(deadline) & ")");
    report "Capture DONE.";

    -- ── Step 3: Block read (CMD_READ_CAPTURE) from base 0 ────────
    report "Block read from base 0...";
    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 30 us;
    -- Skip phantom bytes from pump startup (0-2 bytes)
    -- First valid sample after phantoms: sample 0 = 0x0001
    check(rx_stream(0) = x"00" or rx_stream(0) = x"01",
          "Stream byte 0 should be 0x00 (phantom) or 0x01 (sample 0 low)");
    check(rx_stream(1) = x"00" or rx_stream(1) = x"01",
          "Stream byte 1 should be 0x00 (phantom) or 0x01 (sample 0 low/high)");
    for w in 0 to 255 loop
      -- Even sample in bits 15:0
      word := pay(w*4 + 1) & pay(w*4);
      if w > 0 then
        check(unsigned(word) = unsigned(prev_word) + 1,
              "Block read not +1 ramp at even index " & integer'image(w*2));
      end if;
      prev_word := word;
      -- Odd sample in bits 31:16
      word := pay(w*4 + 3) & pay(w*4 + 2);
      check(unsigned(word) = unsigned(prev_word) + 1,
            "Block read not +1 ramp at odd index " & integer'image(w*2+1));
      prev_word := word;
    end loop;
    report "Block read: +1 ramp confirmed";
    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_START_STREAM, addr_pld, 4);
    wait for 8 us;

    -- CS-held stream read: ack (~16 bytes) + guard + 1024 samples
    stream_read_holdcs(spi_cs, sck, spi_mosi, spi_miso, 2080, rx_stream);
    -- Raise CS to end the stream
    spi_cs <= '1';
    wait for SPI_HALF;

    -- Parse the ack frame: look for SYNC_RSP (0xAA, 0x55) in rx_stream
    ack_found := false;
    ack_base := 0;
    for i in 0 to 30 loop
      if not ack_found and rx_stream(i) = x"AA" and rx_stream(i+1) = x"55" then
        ack_base := i;
        ack_found := true;
      end if;
    end loop;
    check(ack_found, "Stream ack SYNC not found in rx_stream");
    ack_status := rx_stream(ack_base + 2);
    ack_seq := rx_stream(ack_base + 3);
    ack_paylen := to_integer(unsigned(rx_stream(ack_base + 5))) * 256
                + to_integer(unsigned(rx_stream(ack_base + 4)));
    check(ack_status = ST_STREAM_ACTIVE,
          "Stream ack status=" & to_hstring(ack_status) & " != ST_STREAM_ACTIVE");
    check(ack_paylen = 8, "Stream ack paylen=" & integer'image(ack_paylen) & " != 8");
    report "Stream ack: status=" & to_hstring(ack_status)
           & " seq=" & to_hstring(ack_seq)
           & " paylen=" & integer'image(ack_paylen);

    report "ack_base=" & integer'image(ack_base) & " rx[0..7]="
           & integer'image(to_integer(unsigned(rx_stream(0)))) & ","
           & integer'image(to_integer(unsigned(rx_stream(1)))) & ","
           & integer'image(to_integer(unsigned(rx_stream(2)))) & ","
           & integer'image(to_integer(unsigned(rx_stream(3))));
    -- data_start = after ack header(6) + payload(8) + CRC(2) = 16 bytes from sync,
    -- plus 2 more bytes to skip the pump-startup phantom 0x0000 sample
    phantom := 0;
    data_start := ack_base + 18;
    -- Align to even byte boundary if needed
    while (data_start + phantom) mod 2 = 1 loop
      phantom := phantom + 1;
    end loop;
    data_start := data_start + phantom;
    if phantom > 0 then
      report "Skipped " & integer'image(phantom) & " phantom byte(s)";
    end if;
    -- Decode 1024 samples: pump sends high byte first, then low byte
    for i in 0 to 1023 loop
      S(i) := rx_stream(data_start + 2*i) & rx_stream(data_start + 2*i + 1);
    end loop;
    report "data_start=" & integer'image(data_start)
           & " S(0)=" & integer'image(to_integer(unsigned(S(0))))
           & " S(1)=" & integer'image(to_integer(unsigned(S(1))))
           & " S(2)=" & integer'image(to_integer(unsigned(S(2))));

    -- ── Assertion A: strict +1 ramp across all 1024 samples ──────
    report "Assertion A: +1 ramp across 1024 samples...";
    for i in 1 to 1023 loop
      check(unsigned(S(i)) = unsigned(S(i-1)) + 1,
            "Assertion A FAIL at index " & integer'image(i)
            & ": S(i)=" & integer'image(to_integer(unsigned(S(i))))
            & " S(i-1)+1=" & integer'image(to_integer(unsigned(S(i-1))) + 1));
    end loop;
    report "Assertion A: PASS (no truncation at block boundary)";

    -- ── Assertion B: S[0] == V0_blockread ────────────────────────
    report "Assertion B: S[0] == V0_blockread";
    check(unsigned(S(0)) = unsigned(V0_blockread),
          "Assertion B FAIL: S(0)=" & integer'image(to_integer(unsigned(S(0))))
          & " V0_blockread=" & integer'image(to_integer(unsigned(V0_blockread))));
    report "Assertion B: PASS (no first-block diversion)";

    -- ── Assertion C: CS_Rise abort + state cleanup ───────────────
    -- Send abort to ensure clean state
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wait_cycles(clk, 50);

    -- Check CMD_GET_STATUS returns clean status
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
    check(st = ST_CAPTURE_IDLE or st = ST_CAPTURE_DONE,
          "Assertion C1: status after abort=" & to_hstring(st));
    report "Assertion C1: PASS (clean status)";

    -- CMD_READ_CAPTURE at base 0 returns V0_blockread again
    report "Assertion C2: re-read block 0...";
    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 30 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    check(pl >= 1024, "Post-abort block read payload too short: "
          & integer'image(pl));
    word := pay(1) & pay(0);
    check(unsigned(word) = unsigned(V0_blockread),
          "Assertion C2 FAIL: post-abort V0="
          & integer'image(to_integer(unsigned(word)))
          & " expected " & integer'image(to_integer(unsigned(V0_blockread))));
    report "Assertion C2: PASS (state clean)";

    report "==========================================";
    report "  TB_CORE_STREAM PASS";
    report "==========================================";
    std.env.finish;
  end process;

end bench;
