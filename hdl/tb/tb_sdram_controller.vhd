library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_sdram_controller is
  generic (
    CLK_FREQ : natural := 96000000
  );
end tb_sdram_controller;

architecture bench of tb_sdram_controller is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant SDRAM_CLK_DELAY : time := CLK_PERIOD / 4;

  signal clk     : std_logic := '0';
  signal sdram_clk_model : std_logic := '0';
  signal reset_n : std_logic := '0';

  signal addr       : std_logic_vector(21 downto 0) := (others => '0');
  signal byteenable : std_logic_vector(1 downto 0) := "00";
  signal chipselect : std_logic := '0';
  signal writedata  : std_logic_vector(15 downto 0) := (others => '0');
  signal read_n     : std_logic := '1';
  signal write_n    : std_logic := '1';
  signal burst      : std_logic := '0';
  signal readdata   : std_logic_vector(15 downto 0);
  signal readvalid  : std_logic;
  signal waitreq    : std_logic;
  -- Capture burst interface + a model of the pump side: a word queue standing
  -- in for the afifo (normal mode: popped word on q one cycle after pop) and
  -- the registered pop-grant logic.
  signal b_req    : std_logic := '0';
  signal b_addr   : std_logic_vector(21 downto 0) := (others => '0');
  signal b_len    : std_logic_vector(8 downto 0) := (others => '0');
  signal b_stop   : std_logic := '0';
  signal b_pop_ok : std_logic := '0';
  signal b_q      : std_logic_vector(15 downto 0) := (others => '0');
  signal b_pop    : std_logic;
  signal b_wr     : std_logic;
  signal b_busy   : std_logic;
  signal push_valid : std_logic := '0';
  signal push_data  : std_logic_vector(15 downto 0) := (others => '0');
  type word_arr is array (0 to 4095) of std_logic_vector(15 downto 0);
  signal q_mem    : word_arr := (others => (others => '0'));
  signal q_wr_idx : natural := 0;
  signal q_rd_idx : natural := 0;
  signal n_wr_cmds : natural := 0;  -- WRITE words observed on capture_wr
  signal fpush_valid : std_logic := '0';
  signal fpush_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal feed_start  : std_logic := '0';
  signal feed_base, feed_cnt, feed_gap : natural := 0;

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;

  -- Push `count` words (first_data, first_data+1, ...) into the model FIFO,
  -- request one burst at `address`, wait for the controller to take it and
  -- finish, then check it wrote exactly `count` words (unless an abort is
  -- expected, count_expected = false).
  procedure burst_write(
    signal req : out std_logic;
    signal a : out std_logic_vector(21 downto 0);
    signal l : out std_logic_vector(8 downto 0);
    signal pv : out std_logic;
    signal pd : out std_logic_vector(15 downto 0);
    signal busy : in std_logic;
    signal wrs : in natural;
    signal sclk : in std_logic;
    constant address : in natural;
    constant count : in natural;
    constant first_data : in natural;
    constant gap : in natural := 0   -- idle clocks between pushed words
  ) is
    variable w0 : natural;
  begin
    w0 := wrs;
    for i in 0 to count - 1 loop
      pd <= std_logic_vector(to_unsigned(first_data + i, 16));
      pv <= '1';
      wait until rising_edge(sclk);
      pv <= '0';
      for g in 1 to gap loop
        wait until rising_edge(sclk);
      end loop;
    end loop;
    -- A refresh can end the burst early; request the remainder (as the pump does).
    while wrs - w0 < count loop
      a <= std_logic_vector(to_unsigned(address + (wrs - w0), 22));
      l <= std_logic_vector(to_unsigned(count - (wrs - w0), 9));
      req <= '1';
      loop
        wait until rising_edge(sclk);
        exit when busy = '1';
      end loop;
      req <= '0';
      loop
        wait until rising_edge(sclk);
        exit when busy = '0';
      end loop;
      wait until rising_edge(sclk);
      wait until rising_edge(sclk);
    end loop;
    check(wrs - w0 = count, "burst at " & integer'image(address) & ": wrote " &
          integer'image(wrs - w0) & " words, expected " & integer'image(count));
    report "burst_done addr=" & integer'image(address) & " words=" & integer'image(count);
  end procedure;

  procedure avalon_write(
    signal a : out std_logic_vector(21 downto 0);
    signal be : out std_logic_vector(1 downto 0);
    signal cs : out std_logic;
    signal wd : out std_logic_vector(15 downto 0);
    signal wn : out std_logic;
    signal rn : out std_logic;
    signal wreq : in std_logic;
    signal sclk : in std_logic;
    constant address : in std_logic_vector(21 downto 0);
    constant data : in std_logic_vector(15 downto 0)
  ) is
  begin
    wait until rising_edge(sclk);
    a <= address;
    be <= "00";
    cs <= '1';
    wd <= data;
    wn <= '0';
    rn <= '1';
    if wreq = '1' then
      wait until rising_edge(sclk) and wreq = '0';
    end if;
    wait until rising_edge(sclk);
    cs <= '0';
    wn <= '1';
    wait until rising_edge(sclk);
  end procedure;

  procedure avalon_read(
    signal a : out std_logic_vector(21 downto 0);
    signal be : out std_logic_vector(1 downto 0);
    signal cs : out std_logic;
    signal rn : out std_logic;
    signal wn : out std_logic;
    signal wreq : in std_logic;
    signal rvalid : in std_logic;
    variable rdata : out std_logic_vector(15 downto 0);
    signal sclk : in std_logic;
    constant address : in std_logic_vector(21 downto 0)
  ) is
    variable timeout_cycles : natural := 0;
  begin
    report "avalon_read start addr=" & integer'image(to_integer(unsigned(address)));
    wait until rising_edge(sclk);
    a <= address;
    be <= "00";
    cs <= '1';
    rn <= '0';
    wn <= '1';
    -- This controller's public read contract is edge-triggered and completion
    -- is signalled by readdatavalid (the production wrapper does not consume
    -- waitrequest).  A refresh can keep waitrequest high until after the valid
    -- pulse, so watching waitrequest first would miss a legitimate completion.
    timeout_cycles := 0;
    while rvalid = '0' loop
      wait until rising_edge(sclk);
      timeout_cycles := timeout_cycles + 1;
      check(timeout_cycles < 20000, "avalon_read readdatavalid timeout");
    end loop;
    rdata := readdata;
    cs <= '0';
    rn <= '1';
    wait until rising_edge(sclk);
    report "avalon_read done addr=" & integer'image(to_integer(unsigned(address))) &
           " data=" & to_hstring(rdata);
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);
  sdram_clk_model <= transport clk after SDRAM_CLK_DELAY;

  DUT : entity work.SDRAM_Controller
    generic map (CLK_Frequency => CLK_FREQ)
    port map (
      clk_in_clk            => clk,
      reset_reset_n         => reset_n,
      sdram_addr            => sdram_addr,
      sdram_ba              => sdram_ba,
      sdram_cas_n           => sdram_cas_n,
      sdram_cke             => sdram_cke,
      sdram_cs_n            => sdram_cs_n,
      sdram_dq              => sdram_dq,
      sdram_dqm             => sdram_dqm,
      sdram_ras_n           => sdram_ras_n,
      sdram_we_n            => sdram_we_n,
      sdram_s_address       => addr,
      sdram_s_byteenable_n  => byteenable,
      sdram_s_chipselect    => chipselect,
      sdram_s_writedata     => writedata,
      sdram_s_read_n        => read_n,
      sdram_s_write_n       => write_n,
      sdram_s_readdata      => readdata,
      sdram_s_readdatavalid => readvalid,
      sdram_s_waitrequest   => waitreq,
      sdram_s_idle          => open,
      capture_burst_req     => b_req,
      capture_burst_addr    => b_addr,
      capture_burst_len     => b_len,
      capture_burst_stop    => b_stop,
      capture_pop_ok        => b_pop_ok,
      capture_data          => b_q,
      capture_pop           => b_pop,
      capture_wr            => b_wr,
      capture_busy          => b_busy
    );

  -- FIFO model: words pushed by the bench, popped by the controller. q takes
  -- the popped word at the edge that samples pop (data one cycle later).
  -- pop_ok follows the same grant rules as the real pump (fast grant while
  -- plenty of words are queued, otherwise a single grant every fifth cycle).
  feeder : process
  begin
    wait until rising_edge(clk) and feed_start = '1';
    for i in 0 to feed_cnt - 1 loop
      fpush_data <= std_logic_vector(to_unsigned(feed_base + i, 16));
      fpush_valid <= '1';
      wait until rising_edge(clk);
      fpush_valid <= '0';
      for g in 1 to feed_gap loop
        wait until rising_edge(clk);
      end loop;
    end loop;
  end process;

  fifo_model : process(clk)
    variable g1, g2, g3 : std_logic := '0';
    variable avail : natural;
    variable wi, ri : natural := 0;
  begin
    if rising_edge(clk) then
      if push_valid = '1' then
        q_mem(wi mod 4096) <= push_data;
        wi := wi + 1;
      elsif fpush_valid = '1' then
        q_mem(wi mod 4096) <= fpush_data;
        wi := wi + 1;
      end if;
      if b_pop = '1' then
        check(ri < wi, "controller popped an empty FIFO");
        b_q <= q_mem(ri mod 4096);
        ri := ri + 1;
      end if;
      q_wr_idx <= wi;
      q_rd_idx <= ri;
      avail := wi - ri;
      g3 := g2; g2 := g1; g1 := b_pop_ok;
      if avail >= 8 then
        b_pop_ok <= '1';
      elsif avail >= 1 and b_pop_ok = '0' and g1 = '0' and g2 = '0' and g3 = '0' then
        b_pop_ok <= '1';
      else
        b_pop_ok <= '0';
      end if;
      if b_wr = '1' then
        n_wr_cmds <= n_wr_cmds + 1;
      end if;
    end if;
  end process;

  SDRAM_CHIP : entity work.sdram_pin_model
    generic map (CL => 3)
    port map (
      clk   => sdram_clk_model,
      cke   => sdram_cke,
      cs_n  => sdram_cs_n,
      ras_n => sdram_ras_n,
      cas_n => sdram_cas_n,
      we_n  => sdram_we_n,
      ba    => sdram_ba,
      addr  => sdram_addr,
      dqm   => sdram_dqm,
      dq    => sdram_dq
    );

  process
    variable rd : std_logic_vector(15 downto 0);
    variable n6, r6, done6, bursts6 : natural;
  begin
    reset_n <= '0';
    wait_cycles(clk, 10);
    reset_n <= '1';
    wait_cycles(clk, 500);  -- init sequence

    report "=== SDRAM Controller tests ===";

    -- Test 1: Init - check idle
    report "Test 1: Init to idle";
    wait_cycles(clk, 2000);
    check(waitreq = '0' or waitreq = '1', "Waitrequest should be valid");
    report "Test 1: PASS";

    -- Test 2: Single write then read (data integrity)
    report "Test 2: Write then read 0xDEAD at address 0";
    avalon_write(addr, byteenable, chipselect, writedata, write_n, read_n,
                 waitreq, clk, (others => '0'), x"DEAD");
    wait_cycles(clk, 100);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, (others => '0'));
    check(rd = x"DEAD", "Read data mismatch: expected DEAD, got " & to_hstring(rd));
    report "Test 2: PASS";

    -- Test 3: Capture stream crosses the 256-column boundary cleanly
    report "Test 3: Streaming writes across page boundary";
    -- 12 words from column 0xFE: 2 words finish row 0, 10 start row 1.
    burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                16#00FE#, 2, 16#1000#);
    burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                16#0100#, 10, 16#1002#);
    wait_cycles(clk, 200);
    for i in 0 to 11 loop
      avalon_read(addr, byteenable, chipselect, read_n, write_n,
                  waitreq, readvalid, rd, clk,
                  std_logic_vector(to_unsigned(16#00FE# + i, 22)));
      check(rd = std_logic_vector(to_unsigned(16#1000# + i, 16)),
            "Boundary stream mismatch at word " & integer'image(i) &
            ": got " & to_hstring(rd));
      wait_cycles(clk, 8);
    end loop;
    report "Test 3: PASS";

    -- Test 4: Touch first/middle/last addresses of the 64 Mbit range
    report "Test 4: First/middle/last address readback";
    burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                0, 1, 16#A001#);
    burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                2_097_152, 1, 16#B002#);
    burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                4_194_303, 1, 16#C003#);
    wait_cycles(clk, 200);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(0, 22)));
    check(rd = x"A001", "First word mismatch");
    wait_cycles(clk, 8);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(2_097_152, 22)));
    check(rd = x"B002", "Middle word mismatch");
    wait_cycles(clk, 8);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(4_194_303, 22)));
    check(rd = x"C003", "Last word mismatch");
    wait_cycles(clk, 8);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(0, 22)));
    check(rd = x"A001", "Last-address write wrapped onto address 0");
    report "Test 4: PASS";

    -- Test 5: Long streaming burst forces at least one refresh window
    report "Test 5: Refresh-safe streaming burst";
    for r in 0 to 3 loop
      burst_write(b_req, b_addr, b_len, push_valid, push_data, b_busy, n_wr_cmds, clk,
                  16#20000# + r * 256, 256, r * 256);
    end loop;
    wait_cycles(clk, 400);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(16#20000#, 22)));
    check(rd = x"0000", "Refresh burst first word mismatch");
    wait_cycles(clk, 8);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(16#20200#, 22)));
    check(rd = x"0200", "Refresh burst middle word mismatch");
    wait_cycles(clk, 8);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(16#203FF#, 22)));
    check(rd = x"03FF", "Refresh burst last word mismatch");
    report "Test 5: PASS";

    -- Test 6: a sparse burst (one word per 12 clocks, longer than the refresh
    -- interval) is cut short by refresh; the remainder is requested again.
    -- Every word must land exactly once at the right address.
    report "Test 6: Refresh pre-empts a sparse burst";
    n6 := n_wr_cmds;
    done6 := 0;
    bursts6 := 0;
    feed_base <= 16#4000#; feed_cnt <= 256; feed_gap <= 11; feed_start <= '1';
    wait_cycles(clk, 1);
    feed_start <= '0';
    while done6 < 256 loop
      b_addr <= std_logic_vector(to_unsigned(16#30000# + done6, 22));
      b_len  <= std_logic_vector(to_unsigned(256 - done6, 9));
      b_req  <= '1';
      loop wait until rising_edge(clk); exit when b_busy = '1'; end loop;
      b_req <= '0';
      loop wait until rising_edge(clk); exit when b_busy = '0'; end loop;
      wait_cycles(clk, 3);
      done6 := n_wr_cmds - n6;
      bursts6 := bursts6 + 1;
      check(bursts6 < 50, "Test 6: burst loop does not converge");
    end loop;
    report "Test 6: 256 words in " & integer'image(bursts6) & " bursts";
    check(bursts6 >= 2, "Test 6: a sparse burst longer than the refresh interval must be split");
    for i in 0 to 255 loop
      avalon_read(addr, byteenable, chipselect, read_n, write_n,
                  waitreq, readvalid, rd, clk,
                  std_logic_vector(to_unsigned(16#30000# + i, 22)));
      check(rd = std_logic_vector(to_unsigned(16#4000# + i, 16)),
            "Test 6: mismatch at " & integer'image(i) & " got " & to_hstring(rd));
      wait_cycles(clk, 4);
    end loop;
    report "Test 6: PASS";

    -- Test 7: abort (stop) mid-burst: no popped word may be lost.
    report "Test 7: stop mid-burst";
    for i in 0 to 63 loop
      push_data <= std_logic_vector(to_unsigned(16#5000# + i, 16));
      push_valid <= '1';
      wait until rising_edge(clk);
      push_valid <= '0';
    end loop;
    n6 := n_wr_cmds;
    r6 := q_rd_idx;
    b_addr <= std_logic_vector(to_unsigned(16#38000#, 22));
    b_len  <= std_logic_vector(to_unsigned(64, 9));
    b_req  <= '1';
    loop wait until rising_edge(clk); exit when b_busy = '1'; end loop;
    b_req <= '0';
    wait_cycles(clk, 14);
    b_stop <= '1';
    loop wait until rising_edge(clk); exit when b_busy = '0'; end loop;
    b_stop <= '0';
    wait_cycles(clk, 4);
    check(n_wr_cmds - n6 = q_rd_idx - r6, "Test 7: popped " & integer'image(q_rd_idx - r6) &
          " words but wrote " & integer'image(n_wr_cmds - n6));
    check(n_wr_cmds - n6 > 0 and n_wr_cmds - n6 < 64, "Test 7: burst should have been cut short");
    report "Test 7: stopped after " & integer'image(n_wr_cmds - n6) & " words, PASS";
    -- Read back the written prefix.
    for i in 0 to (n_wr_cmds - n6) - 1 loop
      avalon_read(addr, byteenable, chipselect, read_n, write_n,
                  waitreq, readvalid, rd, clk,
                  std_logic_vector(to_unsigned(16#38000# + i, 22)));
      check(rd = std_logic_vector(to_unsigned(16#5000# + i, 16)),
            "Test 7: prefix mismatch at " & integer'image(i) & " got " & to_hstring(rd));
      wait_cycles(clk, 8);
    end loop;


    report "=== ALL SDRAM CONTROLLER TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;
