library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity tb_tiny is end;
architecture sim of tb_tiny is
  constant CLK_PERIOD : time := 20.833 ns;
  signal clk:std_logic:='0'; signal running:boolean:=true;
  signal rate_div:natural:=12; signal samples:natural:=48;
  signal run_f:std_logic:='0'; signal full:std_logic;
  signal inputs:std_logic_vector(15 downto 0):=(others=>'0');
  signal addr:natural:=0; signal outputs:std_logic_vector(15 downto 0);
  signal cont:std_logic:='1';
  signal bf:std_logic_vector(2 downto 0); signal ba:std_logic_vector(2 downto 0):=(others=>'0');
begin
  clk <= not clk after CLK_PERIOD/2 when running;
  fla: entity work.Fast_Logic_Analyzer_SDRAM(rtl) generic map(1048576,16,true)
    port map(clk,open,rate_div,samples,0,run_f,full,inputs,addr,outputs,
      open,open,open,open,open,open,open,open,open,open,open,
      open,open,open,open,cont,bf,ba);
  process begin
    wait for 10 us;
    samples <= 48; run_f <= '1';
    wait until bf(0)='1' for 200 us;
    report "A=" & std_logic'image(bf(0)) severity note;
    wait until bf(1)='1' for 200 us;
    report "B=" & std_logic'image(bf(1)) severity note;
    wait until bf(2)='1' for 200 us;
    if bf(2)='1' then report "C=FULL PASS" severity note;
    else report "C=NOTFULL FAIL" severity failure; end if;
    running<=false; wait;
  end process;
end sim;
