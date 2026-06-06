library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SPI_Slave2 is
  port (
    sys_clk    : in  std_logic;
    fast_clk   : in  std_logic := '0';
    reset      : in  std_logic := '0';
    SCK        : in  std_logic := '0';
    MOSI       : in  std_logic := '0';
    MISO       : out std_logic := 'Z';
    CS_n       : in  std_logic := '1';
    TX_Data    : in  std_logic_vector(7 downto 0) := (others => '0');
    SPI_Preamble   : in  std_logic_vector(7 downto 0) := (others => '0');
    TX_Ready   : out std_logic := '0';
    RX_Data    : out std_logic_vector(7 downto 0) := (others => '0');
    RX_Valid   : out std_logic := '0';
    PipeDepth  : in  natural range 2 to 8 := 8
  );
end SPI_Slave2;

architecture rtl of SPI_Slave2 is
  -- fast_clk domain: full SPI engine (120 MHz, plenty for 30 MHz SCK)
  signal sck_sync    : std_logic := '0';
  signal cs_sync     : std_logic := '0';
  signal sck_prev    : std_logic := '0';
  signal cs_prev     : std_logic := '0';
  signal sck_rise    : std_logic := '0';
  signal sck_fall    : std_logic := '0';
  signal cs_fall     : std_logic := '0';
  signal cs_rise     : std_logic := '0';
  signal cs_active   : std_logic := '0';

  signal rx_shift    : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_shift    : std_logic_vector(7 downto 0) := (others => '0');
  signal bit_cnt     : natural range 0 to 7 := 0;
  signal rx_byte_f   : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid_f  : std_logic := '0';
  signal rx_valid_cnt : natural range 0 to 127 := 0;
  signal reload_pending : std_logic := '0';

  -- TX_Data CDC (sys_clk -> fast_clk): 2-stage, TX_Data stable for full byte
  signal tx_data_s1  : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_data_f   : std_logic_vector(7 downto 0) := (others => '0');
  signal preamble_s1 : std_logic_vector(7 downto 0) := (others => '0');
  signal preamble_f  : std_logic_vector(7 downto 0) := (others => '0');

  -- CDC outputs (fast_clk -> sys_clk)
  signal rx_byte_q   : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid_s1 : std_logic := '0';
  signal rx_valid_s2 : std_logic := '0';
  signal rx_valid_s3 : std_logic := '0';
begin

  -- TX_Data CDC: sys_clk -> fast_clk
  tx_cdc: process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      tx_data_s1  <= TX_Data;
      tx_data_f   <= tx_data_s1;
      preamble_s1 <= SPI_Preamble;
      preamble_f  <= preamble_s1;
    end if;
  end process;

  -- Full SPI engine on fast_clk (120 MHz)
  fast_proc: process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      sck_sync <= SCK;
      cs_sync  <= CS_n;
      sck_prev <= sck_sync;
      cs_prev  <= cs_sync;

      if sck_sync = '1' and sck_prev = '0' then sck_rise <= '1';
      else sck_rise <= '0'; end if;

      if sck_sync = '0' and sck_prev = '1' then sck_fall <= '1';
      else sck_fall <= '0'; end if;

      if cs_sync = '0' and cs_prev = '1' then cs_fall <= '1';
      else cs_fall <= '0'; end if;

      if cs_sync = '1' and cs_prev = '0' then cs_rise <= '1';
      else cs_rise <= '0'; end if;

      if cs_sync = '0' then cs_active <= '1';
      else cs_active <= '0'; end if;

      -- Stretch rx_valid_f to ~50 ns (>1 sys_clk cycle) for reliable CDC
      if rx_valid_cnt > 0 then
        rx_valid_cnt <= rx_valid_cnt - 1;
        rx_valid_f <= '1';
      else
        rx_valid_f <= '0';
      end if;

      if reset = '1' then
        bit_cnt <= 0;
        rx_shift <= (others => '0');
        tx_shift <= (others => '0');
        rx_valid_cnt <= 0;
        rx_valid_f <= '0';
        reload_pending <= '0';

      elsif cs_fall = '1' then
        bit_cnt <= 0;
        tx_shift <= preamble_f;
        rx_valid_cnt <= 0;
        reload_pending <= '0';

      elsif cs_rise = '1' then
        bit_cnt <= 0;
        rx_valid_cnt <= 0;
        reload_pending <= '0';

      elsif cs_active = '1' then
        if sck_rise = '1' then
          rx_shift <= rx_shift(6 downto 0) & MOSI;
          if bit_cnt = 7 then
            rx_byte_f  <= rx_shift(6 downto 0) & MOSI;
            rx_valid_cnt <= 24;  -- hold for ~200 ns at 120 MHz
            bit_cnt    <= 0;
            reload_pending <= '1';
          else
            bit_cnt <= bit_cnt + 1;
          end if;
        end if;

        if sck_fall = '1' then
          if reload_pending = '1' then
            tx_shift       <= tx_data_f;
            reload_pending <= '0';
          else
            tx_shift <= tx_shift(6 downto 0) & '0';
          end if;
        end if;
      end if;
    end if;
  end process;

  MISO <= tx_shift(7) when cs_active = '1' else 'Z';

  -- CDC: rx_byte + rx_valid (fast_clk -> sys_clk)
  -- 3-stage synchronizer on valid, data sampled on rising edge
  rx_cdc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      rx_valid_s1 <= rx_valid_f;
      rx_valid_s2 <= rx_valid_s1;
      rx_valid_s3 <= rx_valid_s2;

      if rx_valid_s2 = '1' and rx_valid_s3 = '0' then
        rx_byte_q <= rx_byte_f;
      end if;
    end if;
  end process;

  RX_Data  <= rx_byte_q;
  RX_Valid <= rx_valid_s3;

  TX_Ready <= '1';

end rtl;
