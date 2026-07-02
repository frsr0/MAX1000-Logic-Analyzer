-- Reproduce the during-capture batched block-read wedge (2026-07-02 HW):
-- with continuous capture armed (producer advancing), a CS-held burst of
-- CMD_READ_CAPTURE requests at production slot spacing must return exactly
-- one ST_OK response per request carrying the REQUESTED address's data.
-- On hardware we observed: stale/wrong-address data, spurious duplicate
-- ST_CAPTURE_IDLE responses ~444 us after each read, and requests being
-- dropped (wedge). Root-cause suspect: the idle-loop prefetch machinery.
--
-- The FLA mock serves sample value = absolute sample index, so a response
-- carrying another address's data (e.g. a stale prefetch block) is detected
-- by content, not just by framing.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use std.env.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_batched_reads is
  generic (
    -- 30 MHz SCK like hardware (16.67 ns half period). The wedge is
    -- timing-dependent: slot spacing vs the WAIT_BLOCK watchdog.
    SPI_HALF   : time := 16.67 ns;
    CLK_FREQ   : natural := 100_000_000;
    N_BLOCKS   : natural := 6;
    -- production slot: 12-byte request + 208 gap + 1056 response pad
    SLOT_PAD   : natural := 1264;
    -- FLA fetch latency in CLK cycles (HW: ~44 us total incl. parse)
    FLA_LATENCY : natural := 2000
  );
end tb_batched_reads;

architecture bench of tb_batched_reads is
  constant CLK_PERIOD  : time := 1 sec / real(CLK_FREQ);
  constant FAST_PERIOD : time := 5 ns;  -- 200 MHz for the SPI slave oversample

  signal clk          : std_logic := '0';
  signal fast_clk     : std_logic := '0';
  signal spi_cs       : std_logic := '1';
  signal spi_sck      : std_logic := '0';
  signal spi_mosi     : std_logic := '1';
  signal spi_miso     : std_logic;
  signal iface_mode   : std_logic;
  signal inputs       : std_logic_vector(31 downto 0) := (others => '0');
  signal rate_div     : natural range 1 to 500000000;
  signal samples      : natural range 1 to 25000;
  signal start_off    : natural range 0 to 25000;
  signal run          : std_logic;
  signal full         : std_logic := '0';
  signal address      : natural range 0 to 24999;
  signal outputs      : std_logic_vector(31 downto 0) := (others => '0');
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div  : std_logic_vector(15 downto 0);
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
  signal debug_ch0_enable : std_logic;

  signal blk_req_tog  : std_logic := '0';
  signal blk_base     : natural range 0 to 25000 := 0;
  signal blk_count    : natural range 0 to 25000 := 0;
  signal auto_renew   : std_logic := '0';
  signal rd_fifo_q    : std_logic_vector(15 downto 0) := (others => '0');
  signal rd_fifo_empty : std_logic := '1';
  signal rd_fifo_rdreq : std_logic := '0';
  signal producer_index : std_logic_vector(31 downto 0) := (others => '0');
  signal oldest_index   : std_logic_vector(31 downto 0) := (others => '0');

  -- capture-in-progress model
  signal producer_nat : natural := 0;

  -- ── helpers ────────────────────────────────────────────────────
  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop r(i*8+7 downto i*8) := b(b'low + i); end loop;
    return r;
  end function;

  -- build a request packet into tx starting at offset off; returns bytes used
  procedure build_req(
    variable tx  : inout byte_array;
    constant off : in natural;
    constant cmd : in std_logic_vector(7 downto 0);
    constant seq : in natural;
    constant payload : in byte_array;
    constant plen : in natural;
    variable used : out natural) is
    variable len_v, crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
    variable hdr : byte_array(0 to 260);
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
    used := 8 + plen;
  end procedure;

begin
  gen_clk(clk, CLK_PERIOD / 2);
  gen_clk(fast_clk, FAST_PERIOD / 2);

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
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      Debug_Ch0_Enable => debug_ch0_enable,
      Blk_Rd_Req_Tog => blk_req_tog, Blk_Rd_Base => blk_base,
      Blk_Rd_Count => blk_count, Auto_Renew => auto_renew,
      Rd_Fifo_Q => rd_fifo_q, Rd_Fifo_Empty => rd_fifo_empty,
      Rd_Fifo_RdReq => rd_fifo_rdreq,
      Producer_Index => producer_index, Oldest_Index => oldest_index
    );

  -- capture model: producer ramps like a 2 MS/s capture (1 sample / 50 CLK)
  process(clk)
    variable div : natural := 0;
  begin
    if rising_edge(clk) then
      if armed = '1' then
        if div = 49 then
          div := 0;
          producer_nat <= producer_nat + 1;
        else
          div := div + 1;
        end if;
      end if;
    end if;
  end process;
  producer_index <= std_logic_vector(to_unsigned(producer_nat, 32));
  oldest_index   <= (others => '0');

  -- ── FLA mock: block read with latency; serves value = sample index ──
  -- Correct rdfifo semantics for the interface drain FSM (showahead OFF):
  -- Empty deasserts when words are available; q updates the cycle after RdReq.
  fla_mock : process(clk)
    variable tog_d1   : std_logic := '0';
    variable pending  : boolean := false;
    variable lat_cnt  : natural := 0;
    variable base_v   : natural := 0;
    variable remain_v : natural := 0;
    variable level    : natural := 0;   -- words available in the fifo model
    variable rdptr    : natural := 0;   -- next sample index to pop
  begin
    if rising_edge(clk) then
      if blk_req_tog /= tog_d1 then
        tog_d1 := blk_req_tog;
        pending := true;
        lat_cnt := FLA_LATENCY;
        base_v := blk_base;
        remain_v := blk_count;
        level := 0;
        rdptr := blk_base;
      end if;

      if pending then
        if lat_cnt > 0 then
          lat_cnt := lat_cnt - 1;
        elsif remain_v > 0 then
          -- fill: one word per clk after the latency (SDRAM is much faster
          -- than the 100 MHz drain, so fill rate is not the constraint)
          level := level + 1;
          remain_v := remain_v - 1;
        end if;
      end if;

      -- pop side (showahead OFF: q valid one cycle after rdreq)
      if rd_fifo_rdreq = '1' and level > 0 then
        rd_fifo_q <= std_logic_vector(to_unsigned(rdptr mod 65536, 16));
        rdptr := rdptr + 1;
        level := level - 1;
      end if;
      if level > 0 then
        rd_fifo_empty <= '0';
      else
        rd_fifo_empty <= '1';
      end if;
    end if;
  end process;

  -- monitor: log every FLA block-read start (prefetch or host request)
  monitor : process(clk)
    variable tog_d : std_logic := '0';
  begin
    if rising_edge(clk) then
      if blk_req_tog /= tog_d then
        tog_d := blk_req_tog;
        report "[MON] block read issued: base=" & integer'image(blk_base)
               & " count=" & integer'image(blk_count)
               & " producer=" & integer'image(producer_nat);
      end if;
    end if;
  end process;

  -- ── Test sequence ──────────────────────────────────────────────
  main : process
    -- one big CS-held burst buffer
    constant BURST_LEN : natural := 64 + N_BLOCKS * SLOT_PAD;
    variable tx : byte_array(0 to BURST_LEN - 1) := (others => x"FF");
    variable rx : byte_array(0 to BURST_LEN - 1);
    variable pl : byte_array(0 to 7);
    variable used : natural;
    variable off : natural := 0;

    -- scan state
    variable n_ok, n_bad, n_dup, n_wrong : natural := 0;
    variable plen_v : natural;
    variable rsp_seq : natural;
    variable seen : std_logic_vector(0 to 255) := (others => '0');
    variable base_expect : natural;
    variable w0 : natural;
    variable sample_v : natural;
    variable ok_this : boolean;

    procedure reg_write(constant regaddr : in std_logic_vector(7 downto 0);
                        constant value   : in natural) is
      variable p : byte_array(0 to 7);
      variable t2 : byte_array(0 to 63) := (others => x"FF");
      variable r2 : byte_array(0 to 63);
      variable u : natural;
      variable v : std_logic_vector(31 downto 0);
    begin
      v := std_logic_vector(to_unsigned(value, 32));
      p(0) := regaddr;
      p(1) := v(7 downto 0); p(2) := v(15 downto 8);
      p(3) := v(23 downto 16); p(4) := v(31 downto 24);
      build_req(t2, 0, x"20", 1, p, 5, u);
      spi_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, t2(0 to 60), r2(0 to 60));
      wait for 2 us;
    end procedure;

    procedure do_arm is
      variable t2 : byte_array(0 to 63) := (others => x"FF");
      variable r2 : byte_array(0 to 63);
      variable p : byte_array(0 to 0);
      variable u : natural;
    begin
      build_req(t2, 0, x"10", 2, p, 0, u);
      spi_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, t2(0 to 60), r2(0 to 60));
      wait for 2 us;
    end procedure;

  begin
    wait for 2 us;

    report "=== BATCHED DURING-CAPTURE READ TEST ===";
    reg_write(x"22", 1);        -- REG_CONT_MODE = 1
    reg_write(x"01", 24000);    -- REG_SAMPLE_COUNT
    -- Regression: the SPI slave delivers each transaction's final byte after
    -- CS rise, which used to flip the parser's sync-hunt parity and silently
    -- swallow the next frame (this ARM packet). The parser's self-healing
    -- hunt must land it on the first try.
    do_arm;

    -- let the "capture" run: producer ramps; the idle prefetch (if present)
    -- will fire once producer >= 512
    wait for 500 us;

    -- build the CS-held burst: N_BLOCKS requests at production slot spacing
    off := 0;
    for i in 0 to N_BLOCKS - 1 loop
      base_expect := 1000 + i * 512;   -- request sample base (byte addr = *2)
      pl(0) := std_logic_vector(to_unsigned((base_expect * 2) mod 256, 8));
      pl(1) := std_logic_vector(to_unsigned(((base_expect * 2) / 256) mod 256, 8));
      pl(2) := x"00"; pl(3) := x"00";
      build_req(tx, off, x"12", 16 + i, pl, 4, used);   -- CMD_READ_CAPTURE
      off := off + SLOT_PAD;  -- request + gap + response pad
    end loop;

    spi_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF,
             tx(0 to off + 40), rx(0 to off + 40));

    -- scan responses
    for i in 0 to off + 32 loop
      if rx(i) = x"AA" and rx(i+1) = x"55" then
        plen_v := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
        rsp_seq := to_integer(unsigned(rx(i+3)));
        if rsp_seq >= 16 and rsp_seq < 16 + N_BLOCKS and plen_v <= 1024 then
          if rx(i+2) = x"00" and plen_v = 1024 then
            -- data response: verify first word = requested base sample
            base_expect := 1000 + (rsp_seq - 16) * 512;
            w0 := to_integer(unsigned(rx(i+7))) * 256 + to_integer(unsigned(rx(i+6)));
            ok_this := true;
            for k in 0 to 7 loop
              sample_v := to_integer(unsigned(rx(i + 7 + 2*k))) * 256
                          + to_integer(unsigned(rx(i + 6 + 2*k)));
              if sample_v /= ((base_expect + k) mod 65536) then
                ok_this := false;
              end if;
            end loop;
            if seen(rsp_seq) = '1' then
              n_dup := n_dup + 1;
              report "DUPLICATE response for seq " & integer'image(rsp_seq)
                     & " at byte " & integer'image(i);
            end if;
            seen(rsp_seq) := '1';
            if ok_this then
              n_ok := n_ok + 1;
            else
              n_wrong := n_wrong + 1;
              report "WRONG-DATA response seq " & integer'image(rsp_seq)
                     & " at byte " & integer'image(i)
                     & ": word0=" & integer'image(w0)
                     & " expected " & integer'image(base_expect);
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
           & " wrong-data=" & integer'image(n_wrong)
           & " non-ok=" & integer'image(n_bad)
           & " duplicates=" & integer'image(n_dup)
           & " of " & integer'image(N_BLOCKS) & " requests";

    check(n_ok = N_BLOCKS, "all requests answered with correct data");
    check(n_wrong = 0, "no stale/wrong-address data served");
    check(n_bad = 0, "no spurious non-OK responses");
    check(n_dup = 0, "no duplicate responses");
    report "=== TB PASSED ===";
    finish;
  end process;

end bench;
