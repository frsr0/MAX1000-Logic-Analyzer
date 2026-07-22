-- Raw-stream teardown cleanliness testbench.
-- Two phases:
--   Phase A: CMD_START_RAW_STREAM -> CS rise -> CMD_READ_CAPTURE -> compare with baseline
--   Phase B: CMD_START_RAW_STREAM -> CS rise -> CMD_ABORT_CAPTURE -> CMD_READ_CAPTURE -> compare with baseline
-- Phase A is expected to RED (residual FIFO contamination). Phase B is expected to PASS (abort clears state).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_raw_stream_teardown is
  generic (SPI_HALF : time := 50 ns);
end tb_raw_stream_teardown;

architecture bench of tb_raw_stream_teardown is
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
    constant payload : in byte_array; constant plen : in natural;
    constant seq : in natural := 0) is
    variable tx : byte_array(0 to 300);
    variable rx : byte_array(0 to 300);
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd;
    tx(3) := std_logic_vector(to_unsigned(seq, 8));
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

  -- CS-held stream command: send packet + ack_pad + stream clocks in one transaction (CS held low).
  procedure stream_command_holdcs(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array;
    constant plen : in natural;
    constant seq : in natural;
    constant ack_pad : in natural;
    constant n_stream : in natural;
    variable rx_data : out byte_array) is
    variable tx : byte_array(0 to rx_data'length - 1);
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
    variable req_len : natural := 8 + plen;
  begin
    for i in tx'range loop
      tx(i) := x"11";
    end loop;
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd;
    tx(3) := std_logic_vector(to_unsigned(seq, 8));
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0);
    tx(7+plen) := crc_v(15 downto 8);
    for i in req_len to req_len + ack_pad - 1 loop
      tx(i) := x"FF";
    end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx, rx_data);
  end procedure;

  -- Stream read: CS held low, clock n_bytes of 0xFF with MISO capture.
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

  -- Block read helper: issue CMD_READ_CAPTURE at given byte address, parse response, fill pay.
  -- Returns status and the full 1024-byte payload in pay(0..1023).
  procedure block_read(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant addr_bytes : in byte_array;  -- 4-byte LE address
    variable status : out std_logic_vector(7 downto 0);
    variable pay : out byte_array;
    variable pay_len : out natural) is
    variable drain : byte_array(0 to 1099);
  begin
    pkt_send(cs_n, sck_o, mosi, miso, CMD_READ_CAPTURE, addr_bytes, 4);
    wait for 30 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 1100, status, pay, pay_len);
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
    -- Reference baseline for block 0 (up to 8 samples)
    type ref_arr is array(0 to 7) of std_logic_vector(15 downto 0);
    variable V0_ref : ref_arr := (others => (others => '0'));
    -- Decoded sample array (up to 512)
    type sample_arr is array(0 to 511) of std_logic_vector(15 downto 0);
    variable S : sample_arr;
    -- Raw stream data
    constant RAW_COUNT : natural := 64;  -- samples to stream
    constant RAW_BYTES : natural := RAW_COUNT * 2;
    constant ACK_PAD : natural := 16;
    constant STREAM_TOTAL : natural := 8 + 8 + ACK_PAD + RAW_BYTES;
    variable rx_stream : byte_array(0 to STREAM_TOTAL - 1);
    variable start_pld : byte_array(0 to 7);
    variable deadline : natural := 0;
    variable a_fail : boolean := false;
    variable b_fail : boolean := false;
    variable mismatch_idx : natural;
    variable i : natural;
    variable poll_status : std_logic_vector(7 downto 0);
  begin
    wait for 30 us;

    report "=== RAW STREAM TEARDOWN TEST ===";

    -- ── Common setup: configure and arm a capture ─────────────────
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
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, poll_status);
      exit when poll_status = ST_CAPTURE_DONE or deadline > 2000;
      deadline := deadline + 1;
      wait for 20 us;
    end loop;
    check(poll_status = ST_CAPTURE_DONE,
          "Capture did not reach DONE (status=" & to_hstring(poll_status) &
          " deadline=" & integer'image(deadline) & ")");
    report "Capture DONE.";

    -- ── Record baseline from block 0 ──────────────────────────────
    report "Recording baseline (block 0)...";
    addr_pld := (x"00", x"00", x"00", x"00");
    block_read(spi_cs, sck, spi_mosi, spi_miso, addr_pld, st, pay, pl);
    check(pl >= 1024, "Baseline block read too short: " & integer'image(pl));
    -- Decode the first 512 samples from the 1024-byte block.
    -- The FLA packs 2 samples per 32-bit entry: even in bits 15:0, odd in 31:16.
    -- On the wire: low byte first -> high byte of low 16 bits -> low byte of high 16 -> high byte.
    -- So for each 4-byte group (4 bytes = 2 samples):
    --   sample[even] = pay[4k+1] & pay[4k]
    --   sample[odd]  = pay[4k+3] & pay[4k+2]
    for w in 0 to 255 loop
      S(w*2)     := pay(w*4 + 1) & pay(w*4);
      S(w*2 + 1) := pay(w*4 + 3) & pay(w*4 + 2);
    end loop;
    -- Store first 8 reference samples
    for i in 0 to 7 loop
      V0_ref(i) := S(i);
    end loop;
    report "Baseline: V0_ref[0]=0x" & to_hstring(V0_ref(0))
           & " V0_ref[1]=0x" & to_hstring(V0_ref(1))
           & " V0_ref[2]=0x" & to_hstring(V0_ref(2))
           & " V0_ref[3]=0x" & to_hstring(V0_ref(3))
           & " V0_ref[4]=0x" & to_hstring(V0_ref(4))
           & " V0_ref[5]=0x" & to_hstring(V0_ref(5))
           & " V0_ref[6]=0x" & to_hstring(V0_ref(6))
           & " V0_ref[7]=0x" & to_hstring(V0_ref(7));

    -- ──────────────────────────────────────────────────────────────────
    -- PHASE A: CS-rise teardown (normal stream end)
    -- Expected: contamination (first samples do NOT match baseline)
    -- ──────────────────────────────────────────────────────────────────
    report "=== Phase A: CS-rise teardown ===";
    report "Phase A: raw stream start (base=0 count=" & integer'image(RAW_COUNT) & ")";

    start_pld(0) := x"00"; start_pld(1) := x"00";
    start_pld(2) := x"00"; start_pld(3) := x"00";
    start_pld(4) := std_logic_vector(to_unsigned(RAW_COUNT mod 256, 8));
    start_pld(5) := std_logic_vector(to_unsigned((RAW_COUNT / 256) mod 256, 8));
    start_pld(6) := x"00"; start_pld(7) := x"00";

    -- CS-held raw stream: send CMD_START_RAW_STREAM, clock ack + pad + RAW_BYTES
    stream_command_holdcs(
      spi_cs, sck, spi_mosi, spi_miso,
      CMD_START_RAW_STREAM, start_pld, 8, 0,
      ACK_PAD, RAW_BYTES, rx_stream);

    -- CS rise: normal teardown
    report "Phase A: CS rise (normal teardown)";
    spi_cs <= '1';
    wait for SPI_HALF * 10;

    -- Post-stream block read
    report "Phase A: block read start";
    addr_pld := (x"00", x"00", x"00", x"00");
    block_read(spi_cs, sck, spi_mosi, spi_miso, addr_pld, st, pay, pl);
    check(pl >= 1024, "Phase A block read too short: " & integer'image(pl));

    -- Decode first 8 samples
    for w in 0 to 3 loop
      S(w*2)     := pay(w*4 + 1) & pay(w*4);
      S(w*2 + 1) := pay(w*4 + 3) & pay(w*4 + 2);
    end loop;

    report "Phase A: S[0]=0x" & to_hstring(S(0))
           & " S[1]=0x" & to_hstring(S(1))
           & " S[2]=0x" & to_hstring(S(2))
           & " S[3]=0x" & to_hstring(S(3));

    -- Assert (expected FAIL / contamination detected):
    -- Check if ANY of S[0..7] deviates from the +1 ramp starting at V0_ref[0]
    a_fail := false;
    for i in 0 to 7 loop
      if unsigned(S(i)) /= unsigned(V0_ref(0)) + i then
        a_fail := true;
        mismatch_idx := i;
      end if;
    end loop;

    if a_fail then
      report "Assertion A: contamination detected (S[" & integer'image(mismatch_idx)
             & "] mismatch) - EXPECTED (Phase A goes RED)" severity note;
    else
      report "Assertion A: WARNING - NO contamination detected on CS-rise teardown"
             severity warning;
    end if;

    -- ──────────────────────────────────────────────────────────────────
    -- PHASE B: Abort teardown (CS rise + CMD_ABORT_CAPTURE)
    -- Expected: clean data matching baseline
    -- ──────────────────────────────────────────────────────────────────
    report "=== Phase B: Abort teardown ===";
    report "Phase B: raw stream start (base=0 count=" & integer'image(RAW_COUNT) & ")";

    start_pld(0) := x"00"; start_pld(1) := x"00";
    start_pld(2) := x"00"; start_pld(3) := x"00";
    start_pld(4) := std_logic_vector(to_unsigned(RAW_COUNT mod 256, 8));
    start_pld(5) := std_logic_vector(to_unsigned((RAW_COUNT / 256) mod 256, 8));
    start_pld(6) := x"00"; start_pld(7) := x"00";

    stream_command_holdcs(
      spi_cs, sck, spi_mosi, spi_miso,
      CMD_START_RAW_STREAM, start_pld, 8, 0,
      ACK_PAD, RAW_BYTES, rx_stream);

    -- CS rise
    report "Phase B: CS rise + CMD_ABORT_CAPTURE";
    spi_cs <= '1';
    wait for SPI_HALF * 10;

    -- Send CMD_ABORT_CAPTURE
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);

    -- Post-abort block read
    report "Phase B: block read start";
    addr_pld := (x"00", x"00", x"00", x"00");
    block_read(spi_cs, sck, spi_mosi, spi_miso, addr_pld, st, pay, pl);
    check(pl >= 1024, "Phase B block read too short: " & integer'image(pl));

    -- Decode all 512 samples
    for w in 0 to 255 loop
      S(w*2)     := pay(w*4 + 1) & pay(w*4);
      S(w*2 + 1) := pay(w*4 + 3) & pay(w*4 + 2);
    end loop;

    report "Phase B: S[0]=0x" & to_hstring(S(0))
           & " S[1]=0x" & to_hstring(S(1))
           & " S[2]=0x" & to_hstring(S(2))
           & " S[3]=0x" & to_hstring(S(3));

    -- Assert B1: first 8 samples EXACTLY match baseline
    b_fail := false;
    for i in 0 to 7 loop
      if unsigned(S(i)) /= unsigned(V0_ref(i)) then
        b_fail := true;
        mismatch_idx := i;
      end if;
    end loop;
    if b_fail then
      report "Assertion B1 FAIL: abort teardown S[" & integer'image(mismatch_idx)
             & "]=" & to_hstring(S(mismatch_idx)) & " expected=" & to_hstring(V0_ref(mismatch_idx))
             severity error;
    else
      report "Assertion B1: ABORT TEARDOWN - first 8 samples match baseline" severity note;
    end if;

    -- Assert B2: all 512 samples form strict +1 ramp
    for i in 1 to 511 loop
      check(unsigned(S(i)) = unsigned(S(i-1)) + 1,
            "Assertion B2 FAIL at sample " & integer'image(i)
            & ": S(i)=" & to_hstring(S(i))
            & " S(i-1)+1=" & to_hstring(std_logic_vector(unsigned(S(i-1)) + 1)));
    end loop;
    report "Assertion B2: All 512 form +1 ramp - PASS" severity note;

    -- Summary
    if a_fail and not b_fail then
      report "==========================================";
      report "  TB_RAW_STREAM_TEARDOWN PASS";
      report "  Phase A (CS-rise): contamination detected (expected)";
      report "  Phase B (abort):   clean - abort DID clear contamination";
      report "==========================================";
    elsif not a_fail and not b_fail then
      report "==========================================";
      report "  TB_RAW_STREAM_TEARDOWN: Phase A passed (no contamination)!";
      report "  Phase B passed. No evidence of the hardware bug in simulation.";
      report "  See Assumptions contingency for next steps.";
      report "==========================================";
    else
      report "==========================================";
      report "  TB_RAW_STREAM_TEARDOWN FAIL";
      if not a_fail then
        report "  Phase A: NO contamination detected (unexpected clean)";
      end if;
      if b_fail then
        report "  Phase B: contamination persists after abort (contradicts HW)";
      end if;
      report "==========================================";
    end if;

    std.env.finish;
  end process;

end bench;
