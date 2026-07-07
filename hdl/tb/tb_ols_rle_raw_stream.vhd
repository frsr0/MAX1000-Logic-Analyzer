-- Unit test for OLS_Interface's CMD_START_RAW_STREAM RLE path.
-- Replaces the SDRAM-side stream with a deterministic fake Rd_Fifo source so
-- the held-CS request+ack+stream behavior can be checked without the full core.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_ols_rle_raw_stream is
  generic (
    CLK_FREQ     : natural := 100_000_000;
    SPI_HALF     : time := 100 ns;
    RUN_SAMPLES  : natural := 4096;
    SAMPLE_COUNT : natural := 4096;
    ACK_PAD_BYTES : natural := 16;
    -- Cycles the fake read FIFO reports empty after each supplied sample. >0
    -- throttles the SDRAM source below the SPI wire rate so the compressor
    -- starves and the DUT must emit 0x0000 idle-filler words (as it does on
    -- real hardware at 30 MHz); the decoder below skips them.
    FIFO_STALL   : natural := 0
  );
end tb_ols_rle_raw_stream;

architecture bench of tb_ols_rle_raw_stream is
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
  signal gen_busy      : std_logic := '0';
  signal gen_fifo_count : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 31;
  signal gen_scl_pin   : natural range 0 to 31;
  signal gen_clear     : std_logic;
  signal gen_i2c_rd_len : natural range 0 to 255;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0);
  signal gen_i2c_test   : std_logic;
  signal gen_spi_test   : std_logic;
  signal gen_repeat     : std_logic;
  signal gen_rs485_pair : std_logic;
  signal armed        : std_logic;
  signal fast_mode    : std_logic;
  signal continuous_mode : std_logic;
  signal narrow_enable   : std_logic;
  signal narrow_channel  : natural range 0 to 15;
  signal analog_enable : std_logic;
  signal analog_only   : std_logic;
  signal analog_profile : std_logic_vector(1 downto 0);
  signal analog_channel : natural range 0 to 31;
  signal buffer_full  : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack   : std_logic_vector(2 downto 0);
  signal pin_map_write : std_logic;
  signal pin_map_channel : natural range 0 to 15;
  signal pin_map_pin : natural range 0 to 31;
  signal debug_ch0_enable : std_logic;
  signal debug_ch0_period : std_logic_vector(31 downto 0);
  signal debug_ch0_duty : std_logic_vector(31 downto 0);
  signal gen_capture_active : std_logic;
  signal gen_start_ack : std_logic := '0';
  signal gen_start_reject : std_logic := '0';
  signal gen_done_pulse : std_logic := '0';
  signal blk_rd_req_tog : std_logic;
  signal blk_rd_base    : natural range 0 to 25000;
  signal blk_rd_count   : natural range 0 to 25000;
  signal auto_renew     : std_logic;
  signal rd_fifo_q      : std_logic_vector(15 downto 0) := (others => '0');
  signal rd_fifo_empty  : std_logic := '1';
  signal rd_fifo_rdreq  : std_logic;
  signal producer_index : std_logic_vector(31 downto 0) := x"00001000";
  signal oldest_index   : std_logic_vector(31 downto 0) := x"00000000";
  signal newest_index   : std_logic_vector(31 downto 0) := x"00000FFF";
  signal overrun_count  : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_valid_cycles   : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_ready_cycles   : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_accept_cycles  : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_stall_cycles   : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_nodata_cycles  : std_logic_vector(31 downto 0) := (others => '0');
  signal pump_overflow_count : std_logic_vector(31 downto 0) := (others => '0');

  signal blk_req_seen : std_logic := '0';
  signal fifo_words_left : natural range 0 to SAMPLE_COUNT := 0;
  signal fifo_idx : natural range 0 to SAMPLE_COUNT := 0;
  signal fifo_pending : std_logic := '0';
  signal fifo_pending_data : std_logic_vector(15 downto 0) := (others => '0');
  signal fifo_stall_ctr : natural range 0 to 65535 := 0;

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  function sample_word(idx : natural) return std_logic_vector is
    variable bit_low : boolean;
  begin
    bit_low := ((idx / RUN_SAMPLES) mod 2) = 0;
    if bit_low then
      return x"FFFE";
    end if;
    return x"FFFF";
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

  procedure pkt_cmd(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable drain_tx : byte_array(0 to 63);
    variable drain_rx : byte_array(0 to 63);
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 8 us;
    for i in drain_tx'range loop drain_tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, drain_tx, drain_rx);
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
    pkt_cmd(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5);
  end procedure;

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
      Gen_Baud_Div => gen_baud_div, Gen_Busy => gen_busy, Gen_Fifo_Count => gen_fifo_count,
      Gen_Proto => gen_proto, Gen_TX_Pin => gen_tx_pin, Gen_SCL_Pin => gen_scl_pin,
      Gen_Clear => gen_clear, Gen_I2C_Rd_Len => gen_i2c_rd_len, Gen_I2C_Dev_R => gen_i2c_dev_r,
      Gen_I2C_Test => gen_i2c_test, Gen_SPI_Test => gen_spi_test, Gen_Repeat => gen_repeat,
      Gen_RS485_Pair => gen_rs485_pair,
      Armed => armed, Fast_Mode => fast_mode, Continuous_Mode => continuous_mode,
      Narrow_Enable => narrow_enable, Narrow_Channel => narrow_channel,
      Analog_Enable => analog_enable, Analog_Only => analog_only,
      Analog_Profile => analog_profile, Analog_Channel => analog_channel,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      Pin_Map_Write => pin_map_write, Pin_Map_Channel => pin_map_channel, Pin_Map_Pin => pin_map_pin,
      Debug_Ch0_Enable => debug_ch0_enable, Debug_Ch0_Period => debug_ch0_period, Debug_Ch0_Duty => debug_ch0_duty,
      Gen_Capture_Active => gen_capture_active, Gen_Start_Ack => gen_start_ack,
      Gen_Start_Reject => gen_start_reject, Gen_Done_Pulse => gen_done_pulse,
      Blk_Rd_Req_Tog => blk_rd_req_tog, Blk_Rd_Base => blk_rd_base, Blk_Rd_Count => blk_rd_count,
      Auto_Renew => auto_renew, Rd_Fifo_Q => rd_fifo_q, Rd_Fifo_Empty => rd_fifo_empty, Rd_Fifo_RdReq => rd_fifo_rdreq,
      Producer_Index => producer_index, Oldest_Index => oldest_index, Newest_Index => newest_index, Overrun_Count => overrun_count,
      Pump_Valid_Cycles => pump_valid_cycles, Pump_Ready_Cycles => pump_ready_cycles,
      Pump_Accept_Cycles => pump_accept_cycles, Pump_Stall_Cycles => pump_stall_cycles,
      Pump_NoData_Cycles => pump_nodata_cycles, Pump_Overflow_Count => pump_overflow_count
    );

  fake_fifo : process(clk)
  begin
    if rising_edge(clk) then
      blk_req_seen <= blk_rd_req_tog;
      if blk_req_seen /= blk_rd_req_tog then
        fifo_idx <= 0;
        fifo_words_left <= blk_rd_count;
        fifo_pending <= '0';
        fifo_stall_ctr <= 0;
      else
        if fifo_pending = '1' then
          rd_fifo_q <= fifo_pending_data;
          fifo_pending <= '0';
        end if;
        if fifo_stall_ctr > 0 then
          fifo_stall_ctr <= fifo_stall_ctr - 1;
        elsif rd_fifo_rdreq = '1' and fifo_words_left > 0 then
          fifo_pending_data <= sample_word(fifo_idx);
          fifo_pending <= '1';
          fifo_idx <= fifo_idx + 1;
          fifo_words_left <= fifo_words_left - 1;
          fifo_stall_ctr <= FIFO_STALL;
        end if;
      end if;
    end if;
  end process;

  -- Report empty while stalling so the compressor genuinely starves.
  rd_fifo_empty <= '1' when fifo_words_left = 0 or fifo_stall_ctr > 0 else '0';

  stim : process
    -- Worst-case RLE output is 4 bytes/sample (run length 1); the +64 margin
    -- covers the ack + compressor-fill offset before the first stream byte so
    -- an incompressible stream is fully captured. The decode loop below stops at
    -- SAMPLE_COUNT, so the surplus idle clocks after the DUT self-terminates are
    -- harmless. (The real host reads incrementally until SAMPLE_COUNT decodes and
    -- needs no such fixed budget.)
    constant STREAM_BYTES : natural := SAMPLE_COUNT * 4 + 64;
    constant TOTAL_BYTES  : natural := 8 + 8 + ACK_PAD_BYTES + STREAM_BYTES;
    variable empty : byte_array(0 to 0);
    variable start_pld : byte_array(0 to 7);
    variable rx_stream : byte_array(0 to TOTAL_BYTES - 1);
    variable sync_at : integer := -1;
    variable ack_status : std_logic_vector(7 downto 0);
    variable ack_paylen : natural;
    variable ack_end : natural;
    variable data_pos : natural;
    variable run_word : std_logic_vector(15 downto 0);
    variable run_count : natural;
    variable run_value : std_logic_vector(15 downto 0);
    variable idle_words : natural := 0;
    variable decoded : natural;
    variable pair_count : natural;
    variable expected_run : natural;
    variable expected_value : std_logic_vector(15 downto 0);
  begin
    wait_cycles(clk, 100);
    report "=== OLS RLE RAW STREAM TEST ===";

    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FLAGS, 16#80000#);
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, spi_sck, spi_mosi, spi_miso, REG_CONT_MODE, 1);
    wait_cycles(clk, 20);

    start_pld(0) := x"00"; start_pld(1) := x"00";
    start_pld(2) := x"00"; start_pld(3) := x"00";
    start_pld(4) := std_logic_vector(to_unsigned(SAMPLE_COUNT mod 256, 8));
    start_pld(5) := std_logic_vector(to_unsigned((SAMPLE_COUNT / 256) mod 256, 8));
    start_pld(6) := x"00"; start_pld(7) := x"00";

    stream_command_holdcs(
      spi_cs, spi_sck, spi_mosi, spi_miso,
      CMD_START_RAW_STREAM, start_pld, 8, 0,
      ACK_PAD_BYTES, STREAM_BYTES, rx_stream);
    spi_cs <= '1';
    wait for SPI_HALF;

    for i in 0 to TOTAL_BYTES - 8 loop
      if sync_at < 0 and rx_stream(i) = x"AA" and rx_stream(i+1) = x"55" then
        sync_at := i;
      end if;
    end loop;
    if sync_at < 0 then
      for i in 0 to 23 loop
        report "rx[" & integer'image(i) & "]=" & to_hstring(rx_stream(i));
      end loop;
    end if;
    check(sync_at >= 0, "RLE stream ack SYNC not found");
    ack_status := rx_stream(sync_at + 2);
    ack_paylen := to_integer(unsigned(rx_stream(sync_at + 5))) * 256
                + to_integer(unsigned(rx_stream(sync_at + 4)));
    check(ack_status = ST_STREAM_ACTIVE,
          "RLE stream ack status=" & to_hstring(ack_status));
    check(ack_paylen = 8, "RLE stream ack paylen=" & integer'image(ack_paylen));

    ack_end := natural(sync_at) + 8 + ack_paylen;
    data_pos := ack_end;
    decoded := 0;
    pair_count := 0;
    expected_value := x"FFFE";

    while data_pos + 3 < TOTAL_BYTES and decoded < SAMPLE_COUNT loop
      -- Skip 0x0000 idle-filler words inserted while the source FIFO was
      -- starved (count words are never 0x0000, so this is unambiguous).
      while data_pos + 1 < TOTAL_BYTES
            and rx_stream(data_pos) = x"00" and rx_stream(data_pos + 1) = x"00" loop
        data_pos := data_pos + 2;
        idle_words := idle_words + 1;
      end loop;
      exit when data_pos + 3 >= TOTAL_BYTES;
      expected_run := RUN_SAMPLES;
      if decoded + expected_run > SAMPLE_COUNT then
        expected_run := SAMPLE_COUNT - decoded;
      end if;
      run_word := rx_stream(data_pos + 1) & rx_stream(data_pos);
      run_count := to_integer(unsigned(run_word));
      run_value := rx_stream(data_pos + 3) & rx_stream(data_pos + 2);
      check(run_count > 0, "zero-count RLE word at byte " & integer'image(data_pos));
      decoded := decoded + run_count;
      check(decoded <= SAMPLE_COUNT, "decoded past requested sample count");
      check(run_value = expected_value,
            "unexpected run value " & to_hstring(run_value) &
            " at pair " & integer'image(pair_count));
      check(run_count = expected_run,
            "unexpected run length " & integer'image(run_count) &
            " at pair " & integer'image(pair_count));
      if expected_value = x"FFFE" then
        expected_value := x"FFFF";
      else
        expected_value := x"FFFE";
      end if;
      pair_count := pair_count + 1;
      data_pos := data_pos + 4;
    end loop;

    check(decoded = SAMPLE_COUNT,
          "decoded " & integer'image(decoded) &
          " samples, expected " & integer'image(SAMPLE_COUNT));
    check(fifo_words_left = 0, "fake FIFO should be fully drained");
    report "decoded " & integer'image(decoded) &
           " samples across " & integer'image(pair_count) & " RLE pairs, skipped " &
           integer'image(idle_words) & " idle words";
    -- When the source is throttled AND the data is compressible (long runs,
    -- so the compressor's output has gaps the drain catches up on), the wire
    -- MUST have carried idle fillers; otherwise the idle-skip path was never
    -- exercised. Incompressible data (run length 1) emits continuously and the
    -- output FIFO never empties, so no idle appears there.
    if FIFO_STALL > 0 and RUN_SAMPLES >= 8 then
      check(idle_words > 0,
            "expected 0x0000 idle-filler words under FIFO_STALL but saw none");
    end if;

    pkt_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
    report "=== TB_OLS_RLE_RAW_STREAM PASS ===";
    stop;
  end process;
end bench;
