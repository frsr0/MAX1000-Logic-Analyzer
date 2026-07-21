library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

-- Single-owner sample budget for the FAST capture producer.
--
-- The interface deliberately exposes events rather than the counter itself:
-- one accepted output word consumes one budget unit.  Continuous mode reloads
-- the configured count on the final accepted word; single-shot mode emits a
-- one-cycle done pulse on that final acceptance.
entity fast_capture_budget is
  generic (
    MAX_COUNT : positive := 4_194_304
  );
  port (
    clk        : in  std_logic;
    rst        : in  std_logic;
    load       : in  std_logic;
    load_count : in  natural range 1 to MAX_COUNT;
    continuous : in  std_logic;
    consume    : in  std_logic;
    budget_open : out std_logic;
    last       : out std_logic;
    done       : out std_logic;
    remaining  : out natural range 0 to MAX_COUNT
  );
end fast_capture_budget;

architecture rtl of fast_capture_budget is
  signal remaining_r : natural range 0 to MAX_COUNT := 0;
  signal reload_r    : natural range 1 to MAX_COUNT := MAX_COUNT;
  signal done_r      : std_logic := '0';
begin
  budget_open <= '1' when remaining_r > 0 else '0';
  last      <= '1' when remaining_r = 1 else '0';
  done      <= done_r;
  remaining <= remaining_r;

  process(clk)
  begin
    if rising_edge(clk) then
      done_r <= '0';
      if rst = '1' then
        remaining_r <= 0;
        reload_r    <= MAX_COUNT;
      elsif load = '1' then
        remaining_r <= load_count;
        reload_r    <= load_count;
      elsif consume = '1' and remaining_r > 0 then
        if remaining_r = 1 then
          if continuous = '1' then
            remaining_r <= reload_r;
          else
            remaining_r <= 0;
            done_r      <= '1';
          end if;
        else
          remaining_r <= remaining_r - 1;
        end if;
      end if;
    end if;
  end process;
end rtl;
