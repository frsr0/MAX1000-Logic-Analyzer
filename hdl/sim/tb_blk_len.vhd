library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_blk_len is
end tb_blk_len;

architecture sim of tb_blk_len is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant SPI_HALF   : time := 500 ns;     -- 1 MHz SPI
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal running  : boolean := true;

  signal spi_cs   : std_logic := '1';
  signal spi_mosi : std_logic := '0';
  signal spi_sck  : std_logic := '0';
  signal uart_rx  : std_logic := '1';

  signal gen_start    : std_logic;
  signal gen_busy     : std_logic := '0';
  signal gen_baud_div : std_logic_vector(15 downto 0);
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we  : std_logic;
  signal gen_tx       : std_logic;

  signal load_count_s : natural := 0;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;
  fast_clk <= not fast_clk after 4.166 ns when running;
  uart_rx <= spi_sck;

  intf : entity work.OLS_Interface
    generic map (CLK_Frequency => 24000000, Def_IFace => 1)
    port map (
      CLK => clk,
      FAST_CLK => fast_clk,
      UART_RX => uart_rx,
      SPI_CS => spi_cs,
      SPI_MOSI => spi_mosi,
      Inputs => (others => '0'),
      Outputs => (others => '0'),
      Buffer_Full => "000",
      Gen_Start => gen_start,
      Gen_Busy => gen_busy,
      Gen_Baud_Div => gen_baud_div,
      Gen_Load_Byte => gen_load_byte,
      Gen_Load_We => gen_load_we
    );

  sig : entity work.Signal_Gen
    port map (
      CLK => clk,
      Load_Byte => gen_load_byte,
      Load_We => gen_load_we,
      Start => gen_start,
      Baud_Div => gen_baud_div,
      Tx_Out => gen_tx,
      Busy => gen_busy
    );

  process(clk)
  begin
    if rising_edge(clk) then
      if gen_load_we = '1' then
        load_count_s <= load_count_s + 1;
      end if;
    end if;
  end process;

  process
    procedure spi_byte(byte : std_logic_vector(7 downto 0)) is
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for i in 7 downto 0 loop
        spi_mosi <= byte(i);
        spi_sck <= '0'; wait for SPI_HALF;
        spi_sck <= '1'; wait for SPI_HALF;
      end loop;
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;

    procedure spi_5byte(b0, b1, b2, b3, b4 : std_logic_vector(7 downto 0)) is
      type byte_array is array (0 to 4) of std_logic_vector(7 downto 0);
      variable bytes : byte_array := (b0, b1, b2, b3, b4);
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for b in 0 to 4 loop
        for i in 7 downto 0 loop
          spi_mosi <= bytes(b)(i);
          spi_sck <= '0'; wait for SPI_HALF;
          spi_sck <= '1'; wait for SPI_HALF;
        end loop;
      end loop;
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;

    procedure spi_data_burst(n : natural) is
      variable dv : std_logic_vector(7 downto 0);
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for b in 0 to n - 1 loop
        dv := std_logic_vector(to_unsigned(b mod 256, 8));
        for i in 7 downto 0 loop
          spi_mosi <= dv(i);
          spi_sck <= '0'; wait for SPI_HALF;
          spi_sck <= '1'; wait for SPI_HALF;
        end loop;
      end loop;
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;

    variable b, a : natural;

  begin
    report "=== blk_len 32-bit decode tests ===" severity note;
    wait for 10 us;

    -- Reset
    report "CMD_RESET..." severity note;
    spi_byte(x"00");
    wait for 5 us;

    -------------------------------------------------------------------
    -- Test 1: blk_len = 16
    -------------------------------------------------------------------
    report "--- blk_len=16 (within range) ---" severity note;
    b := load_count_s;
    spi_5byte(x"A3", x"10", x"00", x"00", x"00");
    wait for 5 us;
    spi_data_burst(16);
    wait for 20 us;
    a := load_count_s;
    if a - b = 16 then
      report "PASS: blk_len=16 loaded " & integer'image(a - b) & " bytes" severity note;
    else
      report "FAIL: blk_len=16 loaded " & integer'image(a - b) & " bytes (expected 16)" severity error;
    end if;

    -------------------------------------------------------------------
    -- Test 2: blk_len = 255
    -------------------------------------------------------------------
    report "--- blk_len=255 (8-bit max) ---" severity note;
    b := load_count_s;
    spi_5byte(x"A3", x"FF", x"00", x"00", x"00");
    wait for 5 us;
    spi_data_burst(255);
    wait for 100 us;
    a := load_count_s;
    if a - b = 255 then
      report "PASS: blk_len=255 loaded " & integer'image(a - b) & " bytes" severity note;
    else
      report "FAIL: blk_len=255 loaded " & integer'image(a - b) & " bytes (expected 255)" severity error;
    end if;

    -------------------------------------------------------------------
    -- Test 3: blk_len = 256
    -------------------------------------------------------------------
    report "--- blk_len=256 (fills FIFO) ---" severity note;
    b := load_count_s;
    spi_5byte(x"A3", x"00", x"01", x"00", x"00");
    wait for 5 us;
    spi_data_burst(256);
    wait for 100 us;
    a := load_count_s;
    if a - b = 256 then
      report "PASS: blk_len=256 loaded " & integer'image(a - b) & " bytes" severity note;
    else
      report "FAIL: blk_len=256 loaded " & integer'image(a - b) & " bytes (expected 256)" severity error;
    end if;

    -------------------------------------------------------------------
    -- Test 4: blk_len = 1024 (clamped) — send exactly 256 bytes
    -- OLD code (data(7 downto 0)) would load 0 (low byte = 0x00).
    -- NEW code clamps to 256 and loads 256.
    -------------------------------------------------------------------
    report "--- blk_len=1024 (clamped to 256) ---" severity note;
    b := load_count_s;
    spi_5byte(x"A3", x"00", x"04", x"00", x"00");
    wait for 5 us;
    spi_data_burst(256);  -- only send expected, avoid confusing state machine with extras
    wait for 100 us;
    a := load_count_s;
    if a - b = 256 then
      report "PASS: blk_len=1024 clamped, loaded " & integer'image(a - b) & " bytes" severity note;
    else
      report "FAIL: blk_len=1024 loaded " & integer'image(a - b) & " bytes (expected 256)" severity error;
    end if;

    -------------------------------------------------------------------
    -- Test 5: blk_len = 4096 (clamped) — send exactly 256 bytes
    -------------------------------------------------------------------
    report "--- blk_len=4096 (clamped to 256) ---" severity note;
    b := load_count_s;
    spi_5byte(x"A3", x"00", x"10", x"00", x"00");
    wait for 5 us;
    spi_data_burst(256);
    wait for 100 us;
    a := load_count_s;
    if a - b = 256 then
      report "PASS: blk_len=4096 clamped, loaded " & integer'image(a - b) & " bytes" severity note;
    else
      report "FAIL: blk_len=4096 loaded " & integer'image(a - b) & " bytes (expected 256)" severity error;
    end if;

    report "*** blk_len TEST COMPLETE ***" severity note;
    running <= false;
    wait;
  end process;
end sim;
