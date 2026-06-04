library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_spi_gen is
end tb_spi_gen;

architecture sim of tb_spi_gen is
  constant CLK_PERIOD : time := 20.833 ns;
  signal clk : std_logic := '0';
  signal running : boolean := true;
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we : std_logic := '0';
  signal start : std_logic := '0';
  signal baud_div : std_logic_vector(15 downto 0) := (others => '0');
  signal proto : std_logic := '0';
  signal spi_mode : std_logic := '0';
  signal tx_out : std_logic;
  signal scl_out : std_logic;
  signal busy : std_logic;
begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.Signal_Gen
    port map (CLK => clk, Load_Byte => load_byte, Load_We => load_we, Start => start,
              Baud_Div => baud_div, Proto => proto, SPI_Mode => spi_mode,
              Tx_Out => tx_out, Scl_Out => scl_out, Busy => busy);

  process
    variable cnt : natural := 0;
    variable phase : integer range 0 to 2 := 2;
    variable last_scl : std_logic := '1';
    variable last_busy : std_logic := '0';
    variable exp : natural := 0;
    variable ok : boolean := true;
  begin
    wait until rising_edge(clk);
    while running loop
      last_scl := scl_out; last_busy := busy;
      wait until rising_edge(clk);
      if spi_mode = '1' then
        if busy = '1' and last_busy = '0' then phase := 0; cnt := 0; end if;
        if busy = '1' then
          cnt := cnt + 1;
          if phase = 0 and scl_out = '0' and last_scl = '1' then
            exp := to_integer(unsigned(baud_div)); phase := 1; cnt := 0;
          elsif phase = 1 and scl_out = '1' and last_scl = '0' then
            if cnt = exp then report "  PASS SPI low @" & integer'image(exp) severity note;
            else report "  FAIL SPI low @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
            phase := 2; cnt := 0;
          elsif phase = 2 and scl_out = '0' and last_scl = '1' then
            if cnt = exp then report "  PASS SPI high @" & integer'image(exp) severity note;
            else report "  FAIL SPI high @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
            phase := 1; cnt := 0;
          end if;
        end if;
      else wait until scl_out'event or busy'event or spi_mode'event; end if;
    end loop;
    wait;
  end process;

  process
    variable cnt : natural := 0;
    variable edges : integer range 0 to 15 := 0;
    variable last_tx : std_logic := '1';
    variable last_busy : std_logic := '0';
    variable exp : natural := 0;
    variable started : boolean := false;
    variable ok : boolean := true;
  begin
    wait until rising_edge(clk);
    while running loop
      last_tx := tx_out; last_busy := busy;
      wait until rising_edge(clk);
      if proto = '0' and spi_mode = '0' then
        if busy = '1' and last_busy = '0' then started := false; cnt := 0; edges := 0; end if;
        if busy = '1' then
          cnt := cnt + 1;
          if tx_out /= last_tx then
            if not started then exp := to_integer(unsigned(baud_div)); started := true; end if;
            edges := edges + 1;
            if edges = 1 then
              if cnt = exp then report "  PASS UART start @" & integer'image(exp) severity note;
              else report "  FAIL UART start @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
              cnt := 0;
            elsif edges <= 9 then
              if cnt = exp then report "  PASS UART bit" & integer'image(edges-1) & " @" & integer'image(exp) severity note;
              else report "  FAIL UART bit" & integer'image(edges-1) & " @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
              cnt := 0;
            end if;
          end if;
        end if;
      else wait until tx_out'event or busy'event; end if;
    end loop;
    wait;
  end process;

  process
    variable cnt : natural := 0;
    variable phase : integer range 0 to 2 := 2;
    variable last_scl : std_logic := '1';
    variable last_busy : std_logic := '0';
    variable exp : natural := 0;
    variable ok : boolean := true;
  begin
    wait until rising_edge(clk);
    while running loop
      last_scl := scl_out; last_busy := busy;
      wait until rising_edge(clk);
      if proto = '1' and spi_mode = '0' then
        if busy = '1' and last_busy = '0' then phase := 0; cnt := 0; end if;
        if busy = '1' then
          cnt := cnt + 1;
          if phase = 0 and scl_out = '0' and last_scl = '1' then
            exp := to_integer(unsigned(baud_div)); phase := 1; cnt := 0;
          elsif phase = 1 and scl_out = '1' and last_scl = '0' then
            if cnt = exp then report "  PASS I2C SCL low @" & integer'image(exp) severity note;
            else report "  FAIL I2C SCL low @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
            phase := 2; cnt := 0;
          elsif phase = 2 and scl_out = '0' and last_scl = '1' then
            if cnt = exp then report "  PASS I2C SCL high @" & integer'image(exp) severity note;
            else report "  FAIL I2C SCL high @" & integer'image(exp) & ": " & integer'image(cnt) severity error; ok := false; end if;
            phase := 1; cnt := 0;
          end if;
        end if;
      else wait until scl_out'event or busy'event; end if;
    end loop;
    wait;
  end process;

  process
    procedure load_byte_proc(byte_val : std_logic_vector(7 downto 0)) is
    begin
      load_byte <= byte_val; load_we <= '1';
      wait until rising_edge(clk); load_we <= '0';
      wait until rising_edge(clk);
    end procedure;
    procedure run_test(mode : string; s_mode : std_logic; p_val : std_logic; bd : natural) is
    begin
      report "" severity note;
      report "=== " & mode & " (Baud_Div=" & integer'image(bd) & ") ===" severity note;
      spi_mode <= s_mode; proto <= p_val;
      baud_div <= std_logic_vector(to_unsigned(bd, 16));
      wait for 2 us;
      load_byte_proc(x"55");
      wait until rising_edge(clk); start <= '1'; wait until rising_edge(clk); start <= '0';
      wait for bd * 30 * CLK_PERIOD + 5 us;  -- enough for 2 bytes
    end procedure;
  begin
    run_test("SPI fast",   '1', '0', 24);
    run_test("SPI default",'1', '0', 416);
    run_test("SPI slow",   '1', '0', 1000);
    run_test("UART",       '0', '0', 416);
    run_test("I2C 100kHz", '0', '1', 240);
    running <= false;
    wait;
  end process;
end sim;
