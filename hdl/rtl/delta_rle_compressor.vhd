library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Merged delta -> RLE readback compressor.
-- The capture compressor produces packed delta words, then a bounded FIFO
-- decouples its bursty output from the exact run-length encoder.
entity delta_rle_compressor is
  port (
    clk                : in  std_logic;
    rst                : in  std_logic;
    sample_in          : in  std_logic_vector(15 downto 0);
    sample_valid       : in  std_logic;
    compression_enable : in  std_logic;
    delta_mode         : in  std_logic;
    flush              : in  std_logic;
    comp_data          : out std_logic_vector(15 downto 0) := (others => '0');
    comp_valid         : out std_logic := '0';
    busy               : out std_logic := '0';
    in_ready           : out std_logic := '1'
  );
end delta_rle_compressor;

architecture rtl of delta_rle_compressor is
  constant FIFO_DEPTH : natural := 16;
  constant FIFO_LAST  : natural := FIFO_DEPTH - 1;
  type fifo_t is array(0 to FIFO_LAST) of std_logic_vector(15 downto 0);
  signal fifo : fifo_t := (others => (others => '0'));
  signal fifo_wr_ptr : natural range 0 to FIFO_LAST := 0;
  signal fifo_rd_ptr : natural range 0 to FIFO_LAST := 0;
  signal fifo_count  : natural range 0 to FIFO_DEPTH := 0;
  signal delta_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal delta_valid : std_logic := '0';
  signal delta_busy  : std_logic := '0';
  signal delta_ready : std_logic := '1';
  signal rle_data    : std_logic_vector(15 downto 0) := (others => '0');
  signal rle_valid   : std_logic := '0';
  signal rle_busy    : std_logic := '0';
  signal rle_ready   : std_logic := '1';
  signal rle_feed    : std_logic := '0';
  signal rle_flush   : std_logic := '0';
  signal rle_input   : std_logic_vector(15 downto 0) := (others => '0');
  signal flush_request : std_logic := '0';
  signal flush_armed   : std_logic := '0';
  signal flush_issued  : std_logic := '0';
begin
  u_delta : entity work.capture_compressor
    port map (
      clk                => clk,
      rst                => rst,
      sample_in          => sample_in,
      sample_valid       => sample_valid,
      compression_enable => compression_enable and delta_mode,
      comp_data          => delta_data,
      comp_valid         => delta_valid,
      busy               => delta_busy,
      in_ready           => delta_ready
    );

  rle_input <= fifo(fifo_rd_ptr) when delta_mode = '1' else sample_in;
  rle_feed <= '1' when compression_enable = '1' and rle_ready = '1' and
                       ((delta_mode = '1' and fifo_count > 0) or
                        (delta_mode = '0' and sample_valid = '1')) else '0';

  u_rle : entity work.rle_compressor
    port map (
      clk                => clk,
      rst                => rst,
      sample_in          => rle_input,
      sample_valid       => rle_feed,
      compression_enable => compression_enable,
      flush              => rle_flush,
      comp_data          => rle_data,
      comp_valid         => rle_valid,
      busy               => rle_busy,
      in_ready           => rle_ready
    );

  rle_flush <= '1' when compression_enable = '1' and
                ((delta_mode = '0' and flush = '1') or
                 (delta_mode = '1' and flush_armed = '1'
                  and fifo_count = 0 and delta_busy = '0'))
               else '0';

  in_ready <= '1' when compression_enable = '0'
                  else '1' when delta_mode = '0' and rle_ready = '1'
                  else '1' when delta_ready = '1'
                        and fifo_count < FIFO_DEPTH
                        and flush_request = '0'
                        and flush_armed = '0'
                        and flush_issued = '0'
                  else '0';

  busy <= '1' when compression_enable = '1'
               and (delta_busy = '1'
                    or (delta_mode = '0' and rle_busy = '1')
                    or fifo_count > 0 or rle_busy = '1'
                    or flush_request = '1' or flush_armed = '1'
                    or flush_issued = '1') else '0';

  process(clk)
    variable wr_ptr_v : natural range 0 to FIFO_LAST;
    variable rd_ptr_v : natural range 0 to FIFO_LAST;
    variable count_v  : natural range 0 to FIFO_DEPTH;
    variable pop_now  : boolean;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        fifo_wr_ptr <= 0; fifo_rd_ptr <= 0; fifo_count <= 0;
        flush_request <= '0'; flush_armed <= '0'; flush_issued <= '0';
        comp_data <= (others => '0'); comp_valid <= '0';
      elsif compression_enable = '0' then
        fifo_wr_ptr <= 0; fifo_rd_ptr <= 0; fifo_count <= 0;
        flush_request <= '0'; flush_armed <= '0'; flush_issued <= '0';
        comp_data <= sample_in; comp_valid <= sample_valid;
      elsif delta_mode = '0' then
        comp_data <= rle_data; comp_valid <= rle_valid;
      else
        comp_valid <= rle_valid;
        if rle_valid = '1' then comp_data <= rle_data; end if;
        if flush = '1' then flush_request <= '1'; end if;
        wr_ptr_v := fifo_wr_ptr; rd_ptr_v := fifo_rd_ptr; count_v := fifo_count;
        if delta_valid = '1' then
          if count_v < FIFO_DEPTH then
            fifo(wr_ptr_v) <= delta_data;
            if wr_ptr_v = FIFO_LAST then wr_ptr_v := 0; else wr_ptr_v := wr_ptr_v + 1; end if;
            count_v := count_v + 1;
          else
            report "delta_rle FIFO overflow" severity failure;
          end if;
        end if;
        pop_now := (count_v > 0) and (rle_ready = '1');
        if pop_now then
          if rd_ptr_v = FIFO_LAST then rd_ptr_v := 0; else rd_ptr_v := rd_ptr_v + 1; end if;
          count_v := count_v - 1;
        end if;
        fifo_wr_ptr <= wr_ptr_v; fifo_rd_ptr <= rd_ptr_v; fifo_count <= count_v;
        if flush_request = '1' and count_v = 0 and delta_busy = '0' then flush_armed <= '1'; end if;
        if flush_armed = '1' and rle_flush = '1' then flush_issued <= '1'; end if;
        if flush_issued = '1' and count_v = 0 and delta_busy = '0'
           and rle_busy = '0' and rle_valid = '0' and rle_flush = '1' then
          flush_request <= '0'; flush_armed <= '0'; flush_issued <= '0';
        end if;
      end if;
    end if;
  end process;
end rtl;
