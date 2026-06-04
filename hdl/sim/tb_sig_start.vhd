library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_sig_start is
end tb_sig_start;

architecture sim of tb_sig_start is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  signal clk : std_logic := '0';
  signal running : boolean := true;
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we : std_logic := '0';
  signal start : std_logic := '0';
  signal baud_div : std_logic_vector(15 downto 0) := x"00D0";  -- 208
  signal proto : std_logic := '0';
  signal spi_mode : std_logic := '0';
  signal tx_out : std_logic;
  signal scl_out : std_logic;
  signal busy : std_logic;
  signal active : std_logic;
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
      SPI_Mode => spi_mode,
      Tx_Out => tx_out,
      Scl_Out => scl_out,
      Busy => busy,
      Active => active
    );

  process
    procedure load_word(b : std_logic_vector(7 downto 0)) is
    begin
      wait until rising_edge(clk);
      load_byte <= b;
      load_we <= '1';
      wait until rising_edge(clk);
      load_we <= '0';
    end procedure;
  begin
    report "=== Signal_Gen Start Test ===" severity note;
    wait for 1 us;

    -- Load 'Hello' into FIFO
    report "Loading 'Hello'..." severity note;
    load_word(x"48");  -- 'H'
    load_word(x"65");  -- 'e'
    load_word(x"6C");  -- 'l'
    load_word(x"6C");  -- 'l'
    load_word(x"6F");  -- 'o'

    -- Start the generator
    report "Starting gen (Start='1' for 1 cycle)..." severity note;
    wait until rising_edge(clk);
    start <= '1';
    wait until rising_edge(clk);
    start <= '0';

    -- Wait for gen to become active
    wait until rising_edge(busy) for 100 us;

    if busy = '1' then
      report "PASS: Gen became active (busy='1')" severity note;
    else
      report "FAIL: Gen never became active" severity error;
    end if;

    -- Wait for transmission to complete
    wait until falling_edge(busy) for 500 us;

    if busy = '0' then
      report "PASS: Gen completed transmission" severity note;
    else
      report "FAIL: Gen didn't finish within 500 us" severity error;
    end if;

    running <= false;
    wait;
  end process;

  -- Measure bit timing
  process
    variable last : time := 0 ns;
    variable cnt : integer := 0;
  begin
    wait on tx_out;
    if tx_out = '0' then
      if last /= 0 ns and cnt < 5 then
        report "Bit period: " & time'image(now - last) severity note;
        cnt := cnt + 1;
      end if;
      last := now;
    end if;
  end process;
end sim;
