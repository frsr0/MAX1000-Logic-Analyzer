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
  signal stream_valid : std_logic := '0';
  signal stream_ready : std_logic;
  signal stream_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal stream_data  : std_logic_vector(15 downto 0) := (others => '0');

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;

  procedure stream_write(
    signal valid : out std_logic;
    signal a : out std_logic_vector(21 downto 0);
    signal d : out std_logic_vector(15 downto 0);
    signal ready : in std_logic;
    signal sclk : in std_logic;
    constant address : in natural;
    constant data : in std_logic_vector(15 downto 0)
  ) is
  begin
    a <= std_logic_vector(to_unsigned(address, 22));
    d <= data;
    valid <= '1';
    loop
      wait until rising_edge(sclk);
      exit when ready = '1';
    end loop;
    report "stream_accept addr=" & integer'image(address) &
           " data=" & to_hstring(data);
    wait until rising_edge(sclk);
    valid <= '0';
    wait until rising_edge(sclk);
    wait until rising_edge(sclk);
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
    if wreq = '1' then
      timeout_cycles := 0;
      while wreq = '1' loop
        wait until rising_edge(sclk);
        timeout_cycles := timeout_cycles + 1;
        check(timeout_cycles < 20000, "avalon_read waitrequest timeout");
      end loop;
    end if;
    wait until rising_edge(sclk);
    cs <= '0';
    rn <= '1';
    if rvalid = '0' then
      timeout_cycles := 0;
      while rvalid = '0' loop
        wait until rising_edge(sclk);
        timeout_cycles := timeout_cycles + 1;
        check(timeout_cycles < 20000, "avalon_read readdatavalid timeout");
      end loop;
    end if;
    rdata := readdata;
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
      capture_stream_valid  => stream_valid,
      capture_stream_ready  => stream_ready,
      capture_stream_addr   => stream_addr,
      capture_stream_data   => stream_data
    );

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
    for i in 0 to 11 loop
      stream_write(stream_valid, stream_addr, stream_data, stream_ready, clk,
                   16#00FE# + i, std_logic_vector(to_unsigned(16#1000# + i, 16)));
    end loop;
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
    stream_write(stream_valid, stream_addr, stream_data, stream_ready, clk,
                 0, x"A001");
    stream_write(stream_valid, stream_addr, stream_data, stream_ready, clk,
                 2_097_152, x"B002");
    stream_write(stream_valid, stream_addr, stream_data, stream_ready, clk,
                 4_194_303, x"C003");
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
    for i in 0 to 1023 loop
      stream_write(stream_valid, stream_addr, stream_data, stream_ready, clk,
                   16#20000# + i, std_logic_vector(to_unsigned(i, 16)));
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


    report "=== ALL SDRAM CONTROLLER TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;
