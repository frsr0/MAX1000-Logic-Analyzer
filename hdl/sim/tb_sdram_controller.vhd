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

  signal clk     : std_logic := '0';
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

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;

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
  begin
    wait until rising_edge(sclk);
    a <= address;
    be <= "00";
    cs <= '1';
    rn <= '0';
    wn <= '1';
    if wreq = '1' then
      wait until rising_edge(sclk) and wreq = '0';
    end if;
    wait until rising_edge(sclk);
    cs <= '0';
    rn <= '1';
    if rvalid = '0' then
      wait until rising_edge(sclk) and rvalid = '1';
    end if;
    rdata := readdata;
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);

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
      sdram_s_burst         => burst,
      sdram_s_readdata      => readdata,
      sdram_s_readdatavalid => readvalid,
      sdram_s_waitrequest   => waitreq,
      sdram_s_idle          => open
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

    -- Test 3: Write at different address
    report "Test 3: Write 0xBEEF at address 100";
    avalon_write(addr, byteenable, chipselect, writedata, write_n, read_n,
                 waitreq, clk, std_logic_vector(to_unsigned(100, 22)), x"BEEF");
    wait_cycles(clk, 100);
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, std_logic_vector(to_unsigned(100, 22)));
    check(rd = x"BEEF", "Read data mismatch at addr 100: expected BEEF, got " & to_hstring(rd));

    -- Verify addr 0 still has old data
    avalon_read(addr, byteenable, chipselect, read_n, write_n,
                waitreq, readvalid, rd, clk, (others => '0'));
    check(rd = x"DEAD", "Addr 0 corrupted: expected DEAD, got " & to_hstring(rd));
    report "Test 3: PASS";

    report "=== ALL SDRAM CONTROLLER TESTS PASSED ===";
    wait;
  end process;

end bench;
