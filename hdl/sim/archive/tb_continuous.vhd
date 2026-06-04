-- Testbench for continuous buffer rotation (fast mode)
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_continuous is
end tb_continuous;

architecture sim of tb_continuous is

  constant CLK_PERIOD : time := 6.667 ns;

  signal clk      : std_logic := '0';
  signal pclk     : std_logic;
  signal rate_div : natural range 1 to 12000000 := 2;
  signal samples  : natural range 1 to 10000 := 3072;
  signal run      : std_logic := '0';
  signal full     : std_logic;
  signal inputs   : std_logic_vector(15 downto 0) := (others => '0');
  signal address  : natural range 0 to 10000 := 0;
  signal outputs  : std_logic_vector(15 downto 0);
  signal fast_mode : std_logic := '1';
  signal fast_clk : std_logic := '0';
  signal armed    : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');
  signal status    : std_logic_vector(7 downto 0);

  signal sdram_addr  : std_logic_vector(11 downto 0);
  signal sdram_ba    : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0) := (others => '0');
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_clk_o : std_logic;
  signal s_burst     : std_logic;

  signal cycle_count : natural range 0 to 65535 := 0;
  signal read_done   : boolean := false;
  signal second_read_done : boolean := false;

  component Fast_Logic_Analyzer_SDRAM is
    generic (
      Max_Samples : natural := 3000000;
      Channels    : natural range 1 to 16 := 16;
      Sim         : boolean := false;
      Write_Latency : natural := 10;
      Read_Latency  : natural := 3;
      Page_Latency  : natural := 3
    );
    port (
      CLK          : in  std_logic;
      CLK_150      : out std_logic;
      Rate_Div     : in  natural range 1 to 12000000 := 12;
      Samples      : in  natural range 1 to Max_Samples := Max_Samples;
      Start_Offset : in  natural range 0 to Max_Samples := 0;
      Run          : in  std_logic := '0';
      Full         : out std_logic := '0';
      Inputs       : in  std_logic_vector(Channels-1 downto 0) := (others => '0');
      Address      : in  natural range 0 to Max_Samples := 0;
      Outputs      : out std_logic_vector(15 downto 0);
      sdram_addr   : out std_logic_vector(11 downto 0);
      sdram_ba     : out std_logic_vector(1 downto 0);
      sdram_cas_n  : out std_logic;
      sdram_dq     : inout std_logic_vector(15 downto 0) := (others => '0');
      sdram_dqm    : out std_logic_vector(1 downto 0);
      sdram_ras_n  : out std_logic;
      sdram_we_n   : out std_logic;
      sdram_cke    : out std_logic := '1';
      sdram_cs_n   : out std_logic := '0';
      sdram_clk    : out std_logic;
      Status       : out std_logic_vector(7 downto 0) := (others => '0');
      s_burst      : out std_logic := '0';
      Armed        : in  std_logic := '0';
      Fast_Mode    : in  std_logic := '0';
      FAST_CLK     : in  std_logic := '0';
      Continuous_Mode : in std_logic := '0';
      Buffer_Full     : out std_logic_vector(2 downto 0) := (others => '0');
      Buffer_Ack      : in std_logic_vector(2 downto 0) := (others => '0')
    );
  end component;

begin

  clk <= not clk after 3.333 ns;

  UUT : Fast_Logic_Analyzer_SDRAM
    generic map (
      Max_Samples => 10000, Channels => 16, Sim => true,
      Write_Latency => 1, Read_Latency => 1, Page_Latency => 1
    )
    port map (
      CLK => clk, CLK_150 => pclk,
      Rate_Div => rate_div, Samples => samples,
      Start_Offset => 0, Run => run, Full => full,
      Inputs => inputs, Address => address, Outputs => outputs,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba,
      sdram_cas_n => sdram_cas_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
      sdram_we_n => sdram_we_n, sdram_cke => sdram_cke,
      sdram_cs_n => sdram_cs_n, sdram_clk => sdram_clk_o,
      Status => status, s_burst => s_burst,
      Armed => armed, Fast_Mode => fast_mode, FAST_CLK => fast_clk,
      Continuous_Mode => continuous_mode,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack
    );

  inputs <= std_logic_vector(to_unsigned(cycle_count mod 65536, 16));

  -- Capture sample counter
  process
    variable cnt : natural := 0;
  begin
    wait until rising_edge(clk);
    if run = '1' then
      cnt := cnt + 1;
      if cnt >= rate_div then
        cnt := 0;
        cycle_count <= cycle_count + 1;
      end if;
    end if;
  end process;

  -- Main test sequence
  process
    variable expected : std_logic_vector(15 downto 0);
  begin
    report "=== CONTINUOUS BUFFER TEST START ===" severity note;

    armed <= '1';
    continuous_mode <= '1';
    fast_mode <= '1';

    -- Start capture
    run <= '1';
    wait for 20 ns;
    report "Capture started, waiting for Full..." severity note;
    wait until rising_edge(full);
    report "=== FULL ASSERTED ===" severity note;
    report "Buffer_Full: " &
      std_logic'image(buffer_full(2)) & " " &
      std_logic'image(buffer_full(1)) & " " &
      std_logic'image(buffer_full(0)) severity note;

    -- Read buffer 0 (1024 samples)
    for addr_val in 0 to 1023 loop
      address <= addr_val;
      wait for 20 ns;
    end loop;

    report "Read complete, acking buffer 0" severity note;
    buffer_ack <= "001";
    wait until rising_edge(clk);
    buffer_ack <= "000";

    -- Wait for full to clear
    wait until falling_edge(full) for 10 us;
    report "Full cleared: " & std_logic'image(full) severity note;

    -- Wait for second Full (buffer 0 refilled)
    wait until rising_edge(full) for 500 us;
    report "Second Full: " & std_logic'image(full) severity note;
    report "Buffer_Full: " &
      std_logic'image(buffer_full(2)) & " " &
      std_logic'image(buffer_full(1)) & " " &
      std_logic'image(buffer_full(0)) severity note;

    if full = '1' then
      -- Read buffer 0 again
      for addr_val in 0 to 1023 loop
        address <= addr_val;
        wait for 20 ns;
      end loop;
      buffer_ack <= "001";
      wait until rising_edge(clk);
      buffer_ack <= "000";
      report "=== SECOND READ + ACK COMPLETE ===" severity note;
    end if;

    second_read_done <= true;
    report "=== TEST COMPLETE ===" severity note;
    wait;
  end process;

end sim;
