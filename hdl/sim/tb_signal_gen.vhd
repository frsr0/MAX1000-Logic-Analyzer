library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_signal_gen is
end tb_signal_gen;

architecture sim of tb_signal_gen is
  constant CLK_PERIOD : time := 41.667 ns;
  signal clk : std_logic := '0';
  signal running : boolean := true;
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we : std_logic := '0';
  signal start : std_logic := '0';
  signal baud_div : std_logic_vector(15 downto 0) := x"0000";
  signal proto : std_logic := '0';
  signal tx_out : std_logic;
  signal busy : std_logic;
begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.Signal_Gen
    port map (
      CLK => clk,
      Load_Byte => load_byte,
      Load_We => load_we,
      Start => start,
      Baud_Div => baud_div,
      Proto => proto,
      Tx_Out => tx_out,
      Busy => busy
    );

  process
  begin
    wait for 1 us;

    report "--- Loading 'Hello' into FIFO ---" severity note;
    for i in 1 to 5 loop
      wait until rising_edge(clk);
      load_we <= '1';
    end loop;
    load_we <= '0';
    wait for 1 us;

    report "--- Starting generator (baud from port = 0, should use FIXED_BAUD) ---" severity note;
    wait until rising_edge(clk);
    start <= '1';
    wait until rising_edge(clk);
    start <= '0';

    wait for 500 us;

    -- Measure Tx_Out bit timing
    report "--- Measuring Tx_Out ---" severity note;
    wait until falling_edge(tx_out);
    wait for 10 ns;
    for i in 1 to 3 loop
      wait until rising_edge(tx_out);
      wait for 10 ns;
      wait until falling_edge(tx_out);
      wait for 10 ns;
      report "Bit period measured" severity note;
    end loop;

    running <= false;
    wait;
  end process;

  -- Monitor process
  process
    variable last : time := 0 ns;
    variable now_t : time;
  begin
    wait on tx_out;
    if tx_out = '0' then
      now_t := now;
      if last /= 0 ns then
        report "Bit time: " & time'image(now_t - last) severity note;
      end if;
      last := now_t;
    end if;
  end process;

end sim;
