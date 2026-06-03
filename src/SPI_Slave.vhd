library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SPI_Slave2 is
  port (
    sys_clk    : in  std_logic;
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
  signal sck_sync : std_logic := '0';
  signal cs_sync  : std_logic := '0';
  signal sck_prev : std_logic := '0';
  signal cs_prev  : std_logic := '0';
  signal sck_rise : std_logic := '0';
  signal sck_fall : std_logic := '0';
  signal cs_fall  : std_logic := '0';
  signal cs_rise  : std_logic := '0';

  signal rx_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal bit_cnt  : natural range 0 to 7 := 0;
  signal rx_byte  : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid_i : std_logic := '0';
  signal reload_pending : std_logic := '0';
  signal reload_sync0   : std_logic := '0';
  signal reload_sync1   : std_logic := '0';
  signal reload_sync2   : std_logic := '0';
  signal reload_sync3   : std_logic := '0';
  signal reload_sync4   : std_logic := '0';
  signal reload_sync5   : std_logic := '0';
  signal reload_sync6   : std_logic := '0';
  signal reload_sync7   : std_logic := '0';
begin

  sync_proc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
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
    end if;
  end process;

  main_proc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      rx_valid_i <= '0';

      if reset = '1' then
        bit_cnt <= 0;
        rx_shift <= (others => '0');
        tx_shift <= (others => '0');
        rx_valid_i <= '0';
        reload_pending <= '0';

      elsif cs_fall = '1' then
        bit_cnt <= 0;
        tx_shift <= SPI_Preamble;
        reload_pending <= '0';

      elsif cs_rise = '1' then
        bit_cnt <= 0;
        reload_pending <= '0';

      elsif cs_sync = '0' then
        if sck_rise = '1' then
          rx_shift <= rx_shift(6 downto 0) & MOSI;
          if bit_cnt = 7 then
            rx_byte <= rx_shift(6 downto 0) & MOSI;
            rx_valid_i <= '1';
            bit_cnt <= 0;
            reload_pending <= '1';
          else
            bit_cnt <= bit_cnt + 1;
          end if;
        end if;

        -- Pipeline: 2-stage delay (reduced from 8; everything is same sys_clk domain)
        reload_sync0 <= reload_pending;
        reload_sync1 <= reload_sync0;

        -- Reload TX_Data onto MISO shift register (2-cycle delay from RX_Valid)
        if sck_fall = '1' and reload_sync1 = '1' then
          tx_shift <= TX_Data;
          reload_pending <= '0';
        elsif sck_fall = '1' then
          tx_shift <= tx_shift(6 downto 0) & '0';
        end if;
      end if;
    end if;
  end process;

  MISO <= tx_shift(7) when cs_sync = '0' else 'Z';
  TX_Ready <= '1';
  RX_Data <= rx_byte;
  RX_Valid <= rx_valid_i;

end rtl;
