library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

-- Two-entry registered valid/ready buffer for the FAST capture stream.
--
-- The buffer never changes out_data while out_valid is asserted and
-- out_ready is low.  It deliberately has no fall-through path: this keeps
-- the producer timing independent of the consumer's ready signal and gives
-- the capture writer a small, deterministic backpressure boundary.
entity fast_capture_elastic_buffer is
  generic (
    DATA_WIDTH : positive := 16
  );
  port (
    clk       : in  std_logic;
    rst       : in  std_logic;
    in_data   : in  std_logic_vector(DATA_WIDTH-1 downto 0);
    in_valid  : in  std_logic;
    in_ready  : out std_logic;
    out_data  : out std_logic_vector(DATA_WIDTH-1 downto 0);
    out_valid : out std_logic;
    out_ready : in  std_logic
  );
end fast_capture_elastic_buffer;

architecture rtl of fast_capture_elastic_buffer is
  signal data0_r  : std_logic_vector(DATA_WIDTH-1 downto 0) := (others => '0');
  signal data1_r  : std_logic_vector(DATA_WIDTH-1 downto 0) := (others => '0');
  signal valid0_r : std_logic := '0';
  signal valid1_r : std_logic := '0';
  signal in_ready_i : std_logic;
begin
  out_data  <= data0_r;
  out_valid <= valid0_r;

  -- A full buffer can accept exactly when its head is being consumed.
  in_ready_i <= not valid1_r or (out_ready and valid0_r);
  in_ready <= in_ready_i;

  process(clk)
    variable pop : std_logic;
    variable push : std_logic;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        valid0_r <= '0';
        valid1_r <= '0';
        data0_r  <= (others => '0');
        data1_r  <= (others => '0');
      else
        pop  := valid0_r and out_ready;
        push := in_valid and in_ready_i;

        if pop = '1' then
          if valid1_r = '1' then
            data0_r  <= data1_r;
            valid0_r <= '1';
            valid1_r <= '0';
          else
            valid0_r <= '0';
          end if;
        end if;

        if push = '1' then
          if pop = '1' then
            -- The freed head slot is filled directly when the second slot
            -- was occupied; otherwise the empty head receives the word.
            if valid1_r = '1' then
              data1_r  <= in_data;
              valid1_r <= '1';
            else
              data0_r  <= in_data;
              valid0_r <= '1';
            end if;
          elsif valid0_r = '0' then
            data0_r  <= in_data;
            valid0_r <= '1';
          else
            data1_r  <= in_data;
            valid1_r <= '1';
          end if;
        end if;
      end if;
    end if;
  end process;
end rtl;
