-- Minimal test: does full_i clear after ack?
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_minimal is
end tb_minimal;

architecture sim of tb_minimal is
  signal clk : std_logic := '0';
  signal pclk : std_logic;
  signal rate_div : natural range 1 to 12000000 := 2;
  signal samples : natural range 1 to 10000 := 3072;
  signal run : std_logic := '0';
  signal full : std_logic;
  signal inputs : std_logic_vector(15 downto 0) := x"AAAA";
  signal address : natural range 0 to 10000 := 0;
  signal outputs : std_logic_vector(15 downto 0);
  signal fast_mode : std_logic := '1';
  signal armed : std_logic := '0';
  signal continuous_mode : std_logic := '1';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack : std_logic_vector(2 downto 0) := "000";
  
  signal sdram_addr: std_logic_vector(11 downto 0);
  signal sdram_ba: std_logic_vector(1 downto 0);
  signal sdram_cas_n: std_logic;
  signal sdram_dq: std_logic_vector(15 downto 0) := (others => '0');
  signal sdram_dqm: std_logic_vector(1 downto 0);
  signal sdram_ras_n: std_logic;
  signal sdram_we_n: std_logic;
  signal sdram_cke: std_logic;
  signal sdram_cs_n: std_logic;
  signal sdram_clk_o: std_logic;
  signal s_burst: std_logic;
  signal status: std_logic_vector(7 downto 0);

  component Fast_Logic_Analyzer_SDRAM is
    generic (Max_Samples: natural:=3000000; Channels: natural:=16;
      Sim: boolean:=false; Write_Latency: natural:=10;
      Read_Latency: natural:=3; Page_Latency: natural:=3);
    port (CLK: in std_logic; CLK_150: out std_logic;
      Rate_Div: in natural range 1 to 12000000:=12;
      Samples: in natural range 1 to Max_Samples:=Max_Samples;
      Start_Offset: in natural range 0 to Max_Samples:=0;
      Run: in std_logic:='0'; Full: out std_logic:='0';
      Inputs: in std_logic_vector(Channels-1 downto 0):=(others=>'0');
      Address: in natural range 0 to Max_Samples:=0;
      Outputs: out std_logic_vector(15 downto 0);
      sdram_addr: out std_logic_vector(11 downto 0);
      sdram_ba: out std_logic_vector(1 downto 0);
      sdram_cas_n: out std_logic;
      sdram_dq: inout std_logic_vector(15 downto 0):=(others=>'0');
      sdram_dqm: out std_logic_vector(1 downto 0);
      sdram_ras_n: out std_logic; sdram_we_n: out std_logic;
      sdram_cke: out std_logic; sdram_cs_n: out std_logic;
      sdram_clk: out std_logic;
      Status: out std_logic_vector(7 downto 0):=(others=>'0');
      s_burst: out std_logic:='0';
      Armed: in std_logic:='0'; Fast_Mode: in std_logic:='0';
      FAST_CLK: in std_logic:='0';
      Continuous_Mode: in std_logic:='0';
      Buffer_Full: out std_logic_vector(2 downto 0):=(others=>'0');
      Buffer_Ack: in std_logic_vector(2 downto 0):=(others=>'0'));
  end component;
begin
  clk <= not clk after 3.333 ns;

  UUT: Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples=>10000, Channels=>16, Sim=>true,
      Write_Latency=>1, Read_Latency=>1, Page_Latency=>1)
    port map (CLK=>clk, CLK_150=>pclk, Rate_Div=>rate_div, Samples=>samples,
      Start_Offset=>0, Run=>run, Full=>full, Inputs=>inputs,
      Address=>address, Outputs=>outputs,
      sdram_addr=>sdram_addr, sdram_ba=>sdram_ba,
      sdram_cas_n=>sdram_cas_n, sdram_dq=>sdram_dq,
      sdram_dqm=>sdram_dqm, sdram_ras_n=>sdram_ras_n,
      sdram_we_n=>sdram_we_n, sdram_cke=>sdram_cke,
      sdram_cs_n=>sdram_cs_n, sdram_clk=>sdram_clk_o,
      Status=>status, s_burst=>s_burst,
      Armed=>armed, Fast_Mode=>fast_mode, FAST_CLK=>'0',
      Continuous_Mode=>continuous_mode,
      Buffer_Full=>buffer_full, Buffer_Ack=>buffer_ack);

  process
  begin
    armed <= '1'; run <= '1';
    wait for 50 us;  -- wait for Full
    assert full = '1' report "Full not asserted!" severity failure;
    report "Full asserted at " & time'image(now) & " status(3)=" & std_logic'image(status(3)) severity note;
    
    -- Ack
    buffer_ack <= "001";
    wait for 10 ns;
    report "10ns after ack: full=" & std_logic'image(full) &
      " status(3)=" & std_logic'image(status(3)) &
      " buf_full=" & std_logic'image(buffer_full(2)) & std_logic'image(buffer_full(1)) & std_logic'image(buffer_full(0)) severity note;
    buffer_ack <= "000";
    wait for 20 ns;
    report "30ns after ack: full=" & std_logic'image(full) &
      " status(3)=" & std_logic'image(status(3)) severity note;
    wait for 1 us;
    report "1us after ack: full=" & std_logic'image(full) &
      " status(3)=" & std_logic'image(status(3)) severity note;
    wait;
  end process;
end sim;
