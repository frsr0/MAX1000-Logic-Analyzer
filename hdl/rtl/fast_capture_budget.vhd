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
  function calc_width(max_value : positive) return positive is
    variable width : natural := 0;
    variable value : natural := max_value;
  begin
    while value > 0 loop
      width := width + 1;
      value := value / 2;
    end loop;
    return width;
  end function;

  constant COUNT_WIDTH : positive := calc_width(MAX_COUNT);
  signal remaining_u : unsigned(COUNT_WIDTH-1 downto 0) := (others => '0');
  signal reload_u    : unsigned(COUNT_WIDTH-1 downto 0) :=
    to_unsigned(MAX_COUNT, COUNT_WIDTH);
  signal done_r      : std_logic := '0';
begin
  budget_open <= '1' when remaining_u /= 0 else '0';
  last      <= '1' when remaining_u = to_unsigned(1, COUNT_WIDTH) else '0';
  done      <= done_r;
  remaining <= to_integer(remaining_u);

  process(clk)
  begin
    if rising_edge(clk) then
      done_r <= '0';
      if rst = '1' then
        remaining_u <= (others => '0');
        reload_u    <= to_unsigned(MAX_COUNT, COUNT_WIDTH);
      elsif load = '1' then
        remaining_u <= to_unsigned(load_count, COUNT_WIDTH);
        reload_u    <= to_unsigned(load_count, COUNT_WIDTH);
      elsif consume = '1' and remaining_u /= 0 then
        if remaining_u = to_unsigned(1, COUNT_WIDTH) then
          if continuous = '1' then
            remaining_u <= reload_u;
          else
            remaining_u <= (others => '0');
            done_r      <= '1';
          end if;
        else
          remaining_u <= remaining_u - 1;
        end if;
      end if;
    end if;
  end process;
end rtl;
