library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

-- mso_stream_mux : round-robin arbiter merging the analog and digital streams
-- ---------------------------------------------------------------------------
-- Sits in the fast_clk domain directly ahead of the async FIFO (dcfifo) write
-- port. It multiplexes two valid/ready producers -- the analog packed-block
-- frames (word bit15='0') and the digital RLE packets (word bit15='1') -- onto
-- the single 16-bit FIFO write interface, one word per clock.
--
-- Arbitration is combinational and fair: `last_served` remembers which stream
-- most recently won, and priority is handed to the *other* stream whenever
-- both have data, giving strict alternation under sustained contention while
-- letting either run at full rate when the other is idle.
--
-- The accept path is combinational (grant AND not-full), so a word is written
-- only on the exact cycle the FIFO can take it -- no registered-write skew that
-- could push a word into a FIFO that just filled. ready is asserted to a
-- producer only when its word is actually being written this cycle, so the
-- valid/ready contract holds with no dropped words. `last_served` is the only
-- state.
--
-- The datapath is a single 16-bit 2:1 mux plus a few arbitration gates -- a
-- shallow combinational path comfortably inside the fast_clk period. At the top
-- of the 400 MHz range, register the FIFO write side behind a 1-deep skid
-- buffer if timing closure needs it.
--
-- NOTE: the outputs are intentionally combinatorial. The downstream packed
-- pipeline (packed_stage -> packed_fifo_wr_r in Fast_Logic_Analyzer_SDRAM)
-- provides 2 register stages before the DCFIFO write port, so adding another
-- register here would only add unnecessary bubble cycles without improving
-- the CDC boundary.
entity mso_stream_mux is
  port (
    clk           : in  std_logic;   -- fast_clk
    rst           : in  std_logic;   -- synchronous, active high

    -- Analog packer port (words carry bit15 = '0')
    analog_data   : in  std_logic_vector(15 downto 0);
    analog_valid  : in  std_logic;
    analog_ready  : out std_logic;

    -- Digital RLE port (words carry bit15 = '1')
    digital_data  : in  std_logic_vector(15 downto 0);
    digital_valid : in  std_logic;
    digital_ready : out std_logic;

    -- Unified write port to the dcfifo
    fifo_wdata    : out std_logic_vector(15 downto 0);
    fifo_wreq     : out std_logic;
    fifo_wfull    : in  std_logic
  );
end mso_stream_mux;

architecture rtl of mso_stream_mux is
  signal last_served  : std_logic := '0';  -- '1' = digital served most recently
  signal grant_dig    : std_logic;
  signal grant_ana    : std_logic;
  signal can_write    : std_logic;
begin

  -- Give priority to the stream that did NOT win last time (fair alternation).
  -- Digital wins when it has data and either it is its turn or analog is idle.
  grant_dig <= '1' when (digital_valid = '1' and
                         (last_served = '0' or analog_valid = '0')) else '0';
  -- Analog wins otherwise, whenever it has data.
  grant_ana <= '1' when (grant_dig = '0' and analog_valid = '1') else '0';

  can_write <= '1' when fifo_wfull = '0' else '0';

  -- Datapath: single 2:1 mux, digital selected when granted else analog.
  fifo_wdata <= digital_data when grant_dig = '1' else analog_data;
  fifo_wreq  <= (grant_dig or grant_ana) and can_write;

  -- Accept a producer only on the cycle its word is actually written.
  digital_ready <= grant_dig and can_write;
  analog_ready  <= grant_ana and can_write;

  process(clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        last_served <= '0';
      else
        -- Update fairness state on a completed write.
        if grant_dig = '1' and can_write = '1' then
          last_served <= '1';
        elsif grant_ana = '1' and can_write = '1' then
          last_served <= '0';
        end if;
      end if;
    end if;
  end process;

end rtl;
