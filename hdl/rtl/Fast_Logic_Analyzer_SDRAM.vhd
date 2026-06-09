library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
library lpm;
use lpm.lpm_components.all;

entity Fast_Logic_Analyzer_SDRAM is
  generic (
    Max_Samples : natural := 3000000;
    Channels    : natural range 1 to 16 := 16;
    Sim         : boolean := false;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3
  );
port (
  CLK          : in  std_logic;
  CLK_150      : out std_logic;
  Rate_Div     : in  natural range 1 to 150000000 := 12;
  Samples      : in  natural range 1 to Max_Samples := Max_Samples;
  Start_Offset : in  natural range 0 to Max_Samples := 0;
  Run          : in  std_logic := '0';
  Full         : out std_logic := '0';
  Inputs       : in  std_logic_vector(Channels-1 downto 0) := (others => '0');
  Address      : in  natural range 0 to Max_Samples := 0;
  Outputs      : out std_logic_vector(15 downto 0);
  sdram_addr   : out std_logic_vector(11 downto 0);
  sdram_ba     : out std_logic_vector(1 downto 0);
  sdram_cas_n  : out std_logic;
  sdram_dq     : inout std_logic_vector(15 downto 0) := (others => '0');
  sdram_dqm    : out std_logic_vector(1 downto 0);
  sdram_ras_n  : out std_logic;
  sdram_we_n   : out std_logic;
   sdram_cke    : out std_logic := '1';
   sdram_cs_n   : out std_logic := '0';
   sdram_clk    : out std_logic;
    Status       : out std_logic_vector(7 downto 0) := (others => '0');
    s_burst      : out std_logic := '0';
    Armed        : in  std_logic := '0';
    Fast_Mode    : in  std_logic := '0';
    FAST_CLK     : in  std_logic := '0';
    -- Double-buffer control
    Continuous_Mode : in std_logic := '0';
    Buffer_Full     : out std_logic_vector(2 downto 0) := (others => '0');
    Buffer_Ack      : in std_logic_vector(2 downto 0) := (others => '0');
    Analog_Frame_Data : in std_logic_vector(63 downto 0) := (others => '0');
    Analog_Frame_Len  : in natural range 1 to 8 := 1;
    Analog_Stream_Mode : in std_logic := '0'
  );
end Fast_Logic_Analyzer_SDRAM;

architecture rtl of Fast_Logic_Analyzer_SDRAM is

  constant sub_steps : natural := 16 / Channels;

  signal pclk : std_logic;

  signal sample_en : std_logic := '0';

  signal s_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal s_wr    : std_logic := '0';
  signal s_wdata : std_logic_vector(15 downto 0) := (others => '0');
  signal s_rd    : std_logic := '0';
  signal s_rdata : std_logic_vector(15 downto 0) := (others => '0');
   signal s_rvalid: std_logic := '0';
   signal s_burst_i : std_logic := '0';
  signal full_i      : std_logic := '0';
  signal run_sync1   : std_logic := '0';
  signal run_sync2   : std_logic := '0';
  signal samples_div_p  : natural range 0 to Max_Samples := 0;
  signal samples_div6_p : natural range 0 to Max_Samples := 0;
  signal samples_d1   : natural range 0 to Max_Samples := 0;
  signal samples_div  : natural range 0 to Max_Samples := 0;
  signal samples_div6 : natural range 0 to Max_Samples := 0;

  -- Pipelined divide-by-3 (LPM_DIVIDE with 4-stage pipeline)
  signal lpm_numer : std_logic_vector(21 downto 0) := (others => '0');
  signal lpm_quot  : std_logic_vector(21 downto 0) := (others => '0');

  -- Write FIFO (depth 16, 38-bit entries: addr(21:0) & wdata(15:0))
  constant FIFO_Depth : natural := 16;
  type fifo_array is array (0 to FIFO_Depth-1) of std_logic_vector(37 downto 0);
  signal fifo_mem  : fifo_array := (others => (others => '0'));
  signal fifo_head_r   : natural range 0 to FIFO_Depth-1 := 0;
  signal fifo_tail_r   : natural range 0 to FIFO_Depth-1 := 0;
  signal fifo_cnt_r    : natural range 0 to FIFO_Depth := 0;
  signal fifo_cnt      : natural range 0 to FIFO_Depth := 0;
  signal buf_limit_r   : natural range 0 to Max_Samples := 0;
  signal buf_last_r    : natural range 0 to Max_Samples := 0;
  signal buf_base0_r   : natural range 0 to Max_Samples := 0;
  signal buf_base1_r   : natural range 0 to Max_Samples := 0;
  signal buf_base2_r   : natural range 0 to Max_Samples := 0;

  -- Pre-trigger BRAM (circular buffer, holds samples before trigger fires)
  constant BRAM_SIZE : natural := 1024;
  type bram_array is array(0 to BRAM_SIZE-1) of std_logic_vector(15 downto 0);
  signal bram : bram_array := (others => (others => '0'));
  attribute ramstyle : string;
  attribute ramstyle of bram : signal is "M9K";
  signal bram_wren  : std_logic := '0';
  signal bram_waddr : natural range 0 to BRAM_SIZE-1 := 0;
  signal bram_wdata : std_logic_vector(15 downto 0) := (others => '0');
  signal bram_raddr : natural range 0 to BRAM_SIZE-1 := 0;
  signal bram_rdata : std_logic_vector(15 downto 0) := (others => '0');

  -- Triple-buffer state
  signal buf_sel    : std_logic_vector(1 downto 0) := "00";
  signal buf_full   : std_logic_vector(2 downto 0) := (others => '0');
  signal full_pending : std_logic := '0';
  signal full_clr_pending : std_logic := '0';

  -- FIFO enqueue pipeline (registered writes to break critical path)
  signal enq_valid0 : boolean := false;
  signal enq_valid1 : boolean := false;
  signal enq_data0  : std_logic_vector(37 downto 0) := (others => '0');
  signal enq_data1  : std_logic_vector(37 downto 0) := (others => '0');
  signal enq_head0  : natural range 0 to FIFO_Depth-1 := 0;
  signal enq_head1  : natural range 0 to FIFO_Depth-1 := 0;

  component SDRAM_Interface is
  generic (
    Sim : boolean := false;
    Write_Latency : natural := 10;
    Read_Latency  : natural := 3;
    Page_Latency  : natural := 3
  );
  port (
    CLK          : in  std_logic;
    Reset        : in  std_logic := '0';
    CLK_150_Out  : out std_logic;
    Address      : in  std_logic_vector(21 downto 0) := (others => '0');
    Write_Enable : in  std_logic := '0';
    Write_Data   : in  std_logic_vector(15 downto 0) := (others => '0');
    Burst        : in  std_logic := '0';
    Read_Enable  : in  std_logic := '0';
    Read_Data    : out std_logic_vector(15 downto 0) := (others => '0');
    Read_Valid   : out std_logic := '0';
    Busy         : out std_logic := '0';
    Idle         : out std_logic := '0';
    sdram_addr   : out std_logic_vector(11 downto 0);
    sdram_ba     : out std_logic_vector(1 downto 0);
    sdram_cas_n  : out std_logic;
    sdram_cke    : out std_logic := '1';
    sdram_cs_n   : out std_logic := '0';
    sdram_dq     : inout std_logic_vector(15 downto 0) := (others => '0');
    sdram_dqm    : out std_logic_vector(1 downto 0);
    sdram_ras_n  : out std_logic;
    sdram_we_n   : out std_logic;
    sdram_clk    : out std_logic
  );
  end component;

begin

  -- 4-stage pipelined divide-by-3 (replaces combinatorial /3 with 38 LUT levels)
  u_div6 : lpm_divide
    generic map (
      LPM_WIDTHN => 22,
      LPM_WIDTHD => 2,
      LPM_NREPRESENTATION => "UNSIGNED",
      LPM_DREPRESENTATION => "UNSIGNED",
      LPM_PIPELINE => 4
    )
    port map (
      clock    => CLK,
      numer    => lpm_numer,
      denom    => "11",
      quotient => lpm_quot,
      remain   => open
    );

  -- Pipeline: register input, LPM divides over 4 cycles, register output
  process(CLK) begin
    if rising_edge(CLK) then
      samples_d1   <= Samples;
      lpm_numer    <= std_logic_vector(to_unsigned(samples_d1, 22));
      samples_div  <= samples_d1;
      samples_div6 <= to_integer(unsigned(lpm_quot));
    end if;
  end process;

  CLK_150 <= pclk;

  -- Divider: assert sample_en for one cycle every Rate_Div PLL clocks
  process (pclk)
     variable cnt : natural range 0 to 150000000 := 0;
  begin
    if rising_edge(pclk) then
      if cnt >= Rate_Div - 1 then
        cnt := 0;
        sample_en <= '1';
      else
        cnt := cnt + 1;
        sample_en <= '0';
      end if;
    end if;
  end process;

  -- BRAM process: synchronous write + registered read (M9K inference template)
  process (pclk)
  begin
    if rising_edge(pclk) then
      if bram_wren = '1' then
        bram(bram_waddr) <= bram_wdata;
      end if;
      bram_rdata <= bram(bram_raddr);
    end if;
  end process;

  -- 2FF synchronizer: Run from CLK domain into pclk domain
  process(pclk)
  begin
    if rising_edge(pclk) then
      run_sync1 <= Run;
      run_sync2 <= run_sync1;
    end if;
  end process;

  -- Re-register CLK-domain divide results into pclk domain; pre-compute buffer limits
  process(pclk)
  begin
    if rising_edge(pclk) then
      samples_div_p  <= samples_div;
      samples_div6_p <= samples_div6;
      buf_limit_r    <= samples_div6;
      if samples_div6 > 0 then
        buf_last_r <= samples_div6 - 1;
      else
        buf_last_r <= 0;
      end if;
      buf_base0_r    <= 0;
      buf_base1_r    <= samples_div6;
      buf_base2_r    <= samples_div6 + samples_div6;
    end if;
  end process;

  -- Main: capture samples to SDRAM via dual buffer, read back from SDRAM
  process (pclk)
    variable step_r  : natural range 0 to sub_steps := 0;
    variable run_r   : std_logic := '0';
    variable rd_mode : boolean := true;
    variable read_addr : natural := 0;
    variable wbuf    : std_logic_vector(15 downto 0) := (others => '0');
    variable waddr_0   : natural range 0 to Max_Samples := 0;
    variable waddr_1   : natural range 0 to Max_Samples := 0;
    variable waddr_2   : natural range 0 to Max_Samples := 0;
    variable a_reg   : natural range 0 to Max_Samples := Max_Samples;
    variable wip        : boolean := false;
    variable wr_cnt   : natural range 0 to 3 := 0;
    variable wr_pend    : boolean := false;
    variable wr_pend_addr : std_logic_vector(21 downto 0) := (others => '0');
    variable wr_pend_data : std_logic_vector(15 downto 0) := (others => '0');
    variable rd_pend : std_logic := '0';
    variable fifo_head_v  : natural range 0 to FIFO_Depth-1 := 0;
    variable fifo_tail_v  : natural range 0 to FIFO_Depth-1 := 0;
    variable fifo_count_v : natural range 0 to FIFO_Depth := 0;
    variable burst_rem   : natural range 0 to 4 := 0;
    variable burst_phase : boolean := false;
    type burst_buf_t is array(0 to 3) of std_logic_vector(37 downto 0);
    variable burst_buf   : burst_buf_t;
    variable bram_wp   : natural range 0 to BRAM_SIZE-1 := 0;
    variable bram_cnt  : natural range 0 to BRAM_SIZE := 0;
    variable bram_post_cnt : natural range 0 to 15000000 := 0;
    variable flush_rem   : natural range 0 to BRAM_SIZE := 0;
    variable flush_idx   : natural range 0 to BRAM_SIZE-1 := 0;
    variable flush_sync : boolean := false;
    variable bram_prepend_sz : natural range 0 to BRAM_SIZE := 0;
    variable write_addr : std_logic_vector(21 downto 0) := (others => '0');
    variable analog_frame : std_logic_vector(63 downto 0) := (others => '0');
    variable analog_len   : natural range 1 to 8 := 1;
    variable analog_idx   : natural range 0 to 7 := 0;
    variable next_word    : std_logic_vector(15 downto 0) := (others => '0');
  begin
    if rising_edge(pclk) then
      bram_wren <= '0';
      fifo_head_v  := fifo_head_r;
      fifo_tail_v  := fifo_tail_r;
      fifo_count_v := fifo_cnt_r;

      -- Commit previous-cycle pending enqueue entries into fifo_mem
      if enq_valid0 then
        fifo_mem(enq_head0) <= enq_data0;
        enq_valid0 <= false;
      end if;
      if enq_valid1 then
        fifo_mem(enq_head1) <= enq_data1;
        enq_valid1 <= false;
      end if;

      -- Buffer ack handling (evaluated every cycle)
      if Buffer_Ack(0) = '1' then
        buf_full(0) <= '0';
        if buf_sel = "00" and buf_full(1) = '1' then
          -- A was waiting to be written (B is full), reset pointer now
          waddr_0 := 0;
        end if;
      end if;
      if Buffer_Ack(1) = '1' then
        buf_full(1) <= '0';
        if buf_sel = "01" and buf_full(0) = '1' then
          -- B was waiting to be written (A is full), reset pointer now
          waddr_1 := 0;
        end if;
      end if;
      if Buffer_Ack(2) = '1' then
        buf_full(2) <= '0';
        if buf_sel = "10" and buf_full(1) = '1' then
          -- C was waiting to be written (B is full), reset pointer now
          waddr_2 := 0;
        end if;
      end if;
      -- Continuous mode backpressure handling
      if Continuous_Mode = '1' then
        if full_i = '1' and (Buffer_Ack(0) = '1' or Buffer_Ack(1) = '1' or Buffer_Ack(2) = '1') then
          full_clr_pending <= '1';
        end if;
        if full_clr_pending = '1' then
          full_i <= '0';
          full_pending <= '0';
          rd_mode := false;
          full_clr_pending <= '0';
          if Fast_Mode = '1' then
            bram_post_cnt := 0;
          end if;
        end if;
        if full_pending = '1' and fifo_count_v = 0
           and not wip and not wr_pend and burst_rem = 0
           and not enq_valid0 and not enq_valid1 then
          full_i <= '1';
          full_pending <= '0';
          rd_mode := true;  -- enter readout so OLS can read completed buffer
        end if;
      end if;

      if run_sync2 /= run_r then
        -- Save pre-trigger BRAM count BEFORE resetting variables
        if run_sync2 = '1' and Fast_Mode = '0' then
          flush_rem := bram_cnt;
          if bram_cnt < BRAM_SIZE then
            flush_idx := 0;
          else
            flush_idx := bram_wp;
          end if;
          flush_sync := false;
        else
          flush_rem := 0;
          flush_idx := 0;
          flush_sync := false;
        end if;
        bram_prepend_sz := flush_rem;  -- save for Full assertion

        waddr_0 := 0; waddr_1 := 0; waddr_2 := 0; step_r := 0;
        fifo_head_v := 0; fifo_tail_v := 0; fifo_count_v := 0;
        enq_valid0 <= false; enq_valid1 <= false;
        wbuf := (others => '0');
        analog_frame := (others => '0');
        analog_len := 1;
        analog_idx := 0;
        bram_wp := 0; bram_cnt := 0;
        rd_pend := '0';
        wr_pend := false; burst_rem := 0; wip := false; wr_cnt := 0;
        buf_sel <= "00";
        buf_full(0) <= '0'; buf_full(1) <= '0'; buf_full(2) <= '0';
        full_i <= '0';
        full_pending <= '0'; full_clr_pending <= '0';
        if run_sync2 = '0' then
          rd_mode := true;
          bram_post_cnt := 0;
        elsif Fast_Mode = '1' then
          rd_mode := false;
          bram_post_cnt := 0;
        else
          rd_mode := false;
        end if;
        run_r := run_sync2;
        s_wr <= '0'; s_rd <= '0';
      end if;

      -- Fast mode pre-trigger
      if Fast_Mode = '1' and Armed = '1' and run_sync2 = '0' and rd_mode then
        rd_mode := false;
        bram_cnt := 0;
      end if;

      if rd_mode then
        -- READOUT
        s_wr <= '0'; s_burst_i <= '0';
        if Fast_Mode = '1' then
          read_addr := Address + Start_Offset;
          if read_addr /= a_reg then
            a_reg := read_addr;
            if read_addr < bram_cnt + bram_post_cnt then
              if bram_cnt + bram_post_cnt <= BRAM_SIZE then
                bram_raddr <= read_addr;
              else
                bram_raddr <= (bram_wp + read_addr) mod BRAM_SIZE;
              end if;
            end if;
          end if;
          if read_addr < bram_cnt + bram_post_cnt then
            Outputs <= bram_rdata;
          else
            Outputs <= (others => '0');
          end if;
        else
          read_addr := Address + Start_Offset;
          if read_addr /= a_reg then
            a_reg := read_addr;
            if read_addr < samples_div_p then
              s_addr <= std_logic_vector(to_unsigned(read_addr, 22));
              s_rd <= '1';
              rd_pend := '1';
            else
              s_rd <= '0';
              rd_pend := '0';
            end if;
          end if;
          if s_rvalid = '1' and rd_pend = '1' then
            Outputs <= s_rdata;
            s_rd <= '0';
            rd_pend := '0';
          elsif read_addr >= samples_div_p then
            Outputs <= (others => '0');
          end if;
        end if;

      else
        -- CAPTURE

        -- Write pump: three mutually exclusive paths.
        if burst_rem > 0 then
          s_burst_i <= '1';
          if not burst_phase then
            s_addr  <= burst_buf(4 - burst_rem)(37 downto 16);
            s_wdata <= burst_buf(4 - burst_rem)(15 downto 0);
            s_wr    <= '1';
            burst_phase := true;
          else
            s_wr        <= '0';
            burst_phase := false;
            burst_rem   := burst_rem - 1;
            if burst_rem = 0 then
              s_burst_i <= '0';
              wr_cnt    := 0;
              wip       := false;
            end if;
          end if;

        elsif wr_pend then
          s_addr  <= wr_pend_addr;
          s_wdata <= wr_pend_data;
          s_wr    <= '1';
          wip     := true;
          wr_pend := false;

        elsif fifo_count_v > 0 and not wip and not enq_valid0 and not enq_valid1 then
          if fifo_count_v >= 4 then
            for i in 0 to 3 loop
              burst_buf(i) := fifo_mem(fifo_tail_v);
              if fifo_tail_v = FIFO_Depth-1 then fifo_tail_v := 0;
              else fifo_tail_v := fifo_tail_v + 1; end if;
              fifo_count_v := fifo_count_v - 1;
            end loop;
            burst_rem   := 4;
            burst_phase := false;
          else
            wr_pend      := true;
            wr_pend_addr := fifo_mem(fifo_tail_v)(37 downto 16);
            wr_pend_data := fifo_mem(fifo_tail_v)(15 downto 0);
            if fifo_tail_v = FIFO_Depth-1 then fifo_tail_v := 0;
            else fifo_tail_v := fifo_tail_v + 1; end if;
            fifo_count_v := fifo_count_v - 1;
          end if;
        end if;

        -- Fast BRAM flush: drain pre-trigger buffer at pclk rate (not sample_en rate).
        -- Completes in ~1024 pclk cycles = ~7 us, losing at most 1 sample at any rate.
        if flush_rem > 0 then
          if fifo_count_v < FIFO_Depth then
            if flush_sync then
              fifo_mem(fifo_head_v) <= std_logic_vector(to_unsigned(waddr_0, 22)) & bram_rdata;
              if fifo_head_v = FIFO_Depth-1 then fifo_head_v := 0;
              else fifo_head_v := fifo_head_v + 1; end if;
              fifo_count_v := fifo_count_v + 1;
              waddr_0 := waddr_0 + 1;
              flush_rem := flush_rem - 1;
            end if;
            flush_sync := true;
            if flush_idx = BRAM_SIZE-1 then flush_idx := 0;
            else flush_idx := flush_idx + 1; end if;
            bram_raddr <= flush_idx;
          end if;
        end if;

        -- Track SDRAM write completion (single writes only)
        if wip and burst_rem = 0 then
          if wr_cnt < 2 then
            wr_cnt := wr_cnt + 1;
          else
            s_wr <= '0'; wip := false; wr_cnt := 0;
          end if;
        end if;

        -- Sample new data when tick arrives
        if sample_en = '1' then
          if Analog_Stream_Mode = '1' then
            if analog_idx = 0 then
              analog_frame := Analog_Frame_Data;
              analog_len := Analog_Frame_Len;
            end if;
            next_word := (others => '0');
            next_word(7 downto 0) := analog_frame((analog_idx * 8) + 7 downto analog_idx * 8);
            wbuf(((step_r + 1) * Channels) - 1 downto step_r * Channels) := next_word;
          else
            wbuf(((step_r + 1) * Channels) - 1 downto step_r * Channels) := Inputs;
          end if;

            if step_r = sub_steps - 1 then
            -- Full 16-bit word ready
            -- ALWAYS compute write address and FIFO data (unconditionally).
            -- This removes the comparator chain from the enq_data0 enable path,
            -- fixing the critical timing path: fast_mode_i -> flush -> Add18 -> LessThan18 -> enq_data0 enable.
            if Continuous_Mode = '1' then
              if buf_sel = "00" then
                write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
              elsif buf_sel = "01" then
                write_addr := std_logic_vector(to_unsigned(buf_base1_r + waddr_1, 22));
              else
                write_addr := std_logic_vector(to_unsigned(buf_base2_r + waddr_2, 22));
              end if;
            else
              write_addr := std_logic_vector(to_unsigned(waddr_0, 22));
            end if;
            enq_data0 <= write_addr & wbuf(15 downto 0);
            enq_head0 <= fifo_head_v;
            if sub_steps > 1 then
              enq_data1 <= std_logic_vector(unsigned(write_addr) + 1) & wbuf(31 downto 16);
              -- Pre-compute next head for upper word (fifo_head_v after increment)
              if fifo_head_v = FIFO_Depth-1 then
                enq_head1 <= 0;
              else
                enq_head1 <= fifo_head_v + 1;
              end if;
            end if;

            if Armed = '1' and run_sync2 = '0' then
              -- Pre-trigger BRAM (circular)
              bram_waddr <= bram_wp;
              bram_wdata <= wbuf;
              bram_wren <= '1';
              if bram_wp = BRAM_SIZE-1 then bram_wp := 0;
              else bram_wp := bram_wp + 1; end if;
              if bram_cnt < BRAM_SIZE then bram_cnt := bram_cnt + 1; end if;
            elsif Fast_Mode = '1' and Armed = '1' then
              -- Fast mode post-trigger
              bram_waddr <= bram_wp;
              bram_wdata <= wbuf;
              bram_wren <= '1';
              if bram_wp = BRAM_SIZE-1 then bram_wp := 0;
              else bram_wp := bram_wp + 1; end if;
              if bram_cnt < BRAM_SIZE then bram_cnt := bram_cnt + 1; end if;
              bram_post_cnt := bram_post_cnt + 1;
            elsif (sub_steps = 1 and fifo_count_v < FIFO_Depth
                                and not enq_valid0 and not enq_valid1) or
                  (sub_steps > 1 and fifo_count_v < FIFO_Depth - 1
                                and not enq_valid0 and not enq_valid1) then
              -- Post-trigger: write to current buffer via FIFO
              if Continuous_Mode = '1' then
                if buf_full(0) = '1' and buf_full(1) = '1' and buf_full(2) = '1' then
                  null;  -- all 3 full: stall, no write
                else
                  -- Buffer-full and pointer logic
                  if buf_sel = "00" then
                    if waddr_0 >= buf_last_r then
                      buf_full(0) <= '1';
                      if buf_full(1) = '1' and buf_full(2) = '1' then
                        full_pending <= '1';
                      else
                        if buf_full(1) = '0' then buf_sel <= "01"; waddr_1 := 0;
                        else buf_sel <= "10"; waddr_2 := 0; end if;
                      end if;
                    end if;
                    waddr_0 := waddr_0 + 1;
                  elsif buf_sel = "01" then
                    if waddr_1 >= buf_last_r then
                      buf_full(1) <= '1';
                      if buf_full(0) = '1' and buf_full(2) = '1' then
                        full_pending <= '1';
                      else
                        if buf_full(2) = '0' then buf_sel <= "10"; waddr_2 := 0;
                        else buf_sel <= "00"; waddr_0 := 0; end if;
                      end if;
                    end if;
                    waddr_1 := waddr_1 + 1;
                  else
                    -- buf_sel = "10": write to buffer C
                    if waddr_2 >= buf_last_r then
                      buf_full(2) <= '1';
                      if buf_full(0) = '1' and buf_full(1) = '1' then
                        full_pending <= '1';
                      else
                        if buf_full(0) = '0' then buf_sel <= "00"; waddr_0 := 0;
                        else buf_sel <= "01"; waddr_1 := 0; end if;
                      end if;
                    end if;
                    waddr_2 := waddr_2 + 1;
                  end if;
                  -- Commit to FIFO (enq_data0/enq_data1/enq_head0/enq_head1 already set above)
                  enq_valid0 <= true;
                  if fifo_head_v = FIFO_Depth-1 then fifo_head_v := 0;
                  else fifo_head_v := fifo_head_v + 1; end if;
                  fifo_count_v := fifo_count_v + 1;
                  -- If sub_steps=2, write upper 16 bits to next address
                  if sub_steps > 1 then
                    enq_valid1 <= true;
                    if fifo_head_v = FIFO_Depth-1 then fifo_head_v := 0;
                    else fifo_head_v := fifo_head_v + 1; end if;
                    fifo_count_v := fifo_count_v + 1;
                  end if;
                end if;
              else
                -- Single-buffer mode (legacy)
                -- Stop at target to let FIFO drain, then Full fires
                if waddr_0 < samples_div_p then
                  -- Commit to FIFO (enq_data0/enq_data1/enq_head0/enq_head1 already set above)
                  enq_valid0 <= true;
                  if fifo_head_v = FIFO_Depth-1 then fifo_head_v := 0;
                  else fifo_head_v := fifo_head_v + 1; end if;
                  fifo_count_v := fifo_count_v + 1;
                  -- If sub_steps=2, write upper 16 bits to next address
                  if sub_steps > 1 then
                    enq_valid1 <= true;
                    if fifo_head_v = FIFO_Depth-1 then fifo_head_v := 0;
                    else fifo_head_v := fifo_head_v + 1; end if;
                    fifo_count_v := fifo_count_v + 1;
                    waddr_0 := waddr_0 + 2;
                  else
                    waddr_0 := waddr_0 + 1;
                  end if;
                end if;
              end if;
            end if;
          end if;

          if step_r = sub_steps - 1 then step_r := 0;
          else step_r := step_r + 1;
          end if;
          if Analog_Stream_Mode = '1' then
            if analog_idx + 1 >= analog_len then
              analog_idx := 0;
            else
              analog_idx := analog_idx + 1;
            end if;
          end if;
        end if;

        -- Assert Full
        if not rd_mode and full_i = '0' then
          if Fast_Mode = '1' then
            if bram_post_cnt >= samples_div_p then
              full_i <= '1';
              rd_mode := true;
              if Continuous_Mode = '1' then
                buf_full(0) <= '1';
              end if;
            end if;
          elsif Continuous_Mode = '1' then
            -- Backpressure handled at top of process (full_pending logic)
            null;
          else
            -- Single-buffer mode: Full when waddr_0 reaches target
            if waddr_0 >= samples_div_p
               and fifo_count_v = 0
               and not wip
               and not wr_pend
               and burst_rem = 0
               and not enq_valid0
               and not enq_valid1
            then
              full_i <= '1';
              rd_mode := true;
            end if;
          end if;
        end if;
      end if;

      -- Commit next-state values to registered signals
      fifo_head_r <= fifo_head_v;
      fifo_tail_r <= fifo_tail_v;
      fifo_cnt_r  <= fifo_count_v;

      -- Drive fifo_cnt signal for external visibility
      fifo_cnt <= fifo_count_v;

      -- Status
      Status(0) <= run_r;
      if wip then Status(1) <= '1'; else Status(1) <= '0'; end if;
      Status(2) <= s_rd;
      Status(3) <= full_i;
      if    fifo_count_v >= 8 then Status(7) <= '1'; else Status(7) <= '0'; end if;
      if    fifo_count_v = 4 or fifo_count_v = 5 or fifo_count_v = 6 or fifo_count_v = 7
         or fifo_count_v = 12 or fifo_count_v = 13 or fifo_count_v = 14 or fifo_count_v = 15
         then Status(6) <= '1'; else Status(6) <= '0'; end if;
      if    fifo_count_v = 2 or fifo_count_v = 3 or fifo_count_v = 6 or fifo_count_v = 7
         or fifo_count_v = 10 or fifo_count_v = 11 or fifo_count_v = 14 or fifo_count_v = 15
         then Status(5) <= '1'; else Status(5) <= '0'; end if;
      if    fifo_count_v = 1 or fifo_count_v = 3 or fifo_count_v = 5 or fifo_count_v = 7
         or fifo_count_v = 9 or fifo_count_v = 11 or fifo_count_v = 13 or fifo_count_v = 15
         then Status(4) <= '1'; else Status(4) <= '0'; end if;
    end if;
  end process;

  Full <= full_i;
  s_burst <= s_burst_i;
  Buffer_Full(0) <= buf_full(0);
  Buffer_Full(2) <= buf_full(2);
  Buffer_Full(1) <= buf_full(1);

  SDRAM_Interface1 : SDRAM_Interface
  generic map (Sim => Sim, Write_Latency => Write_Latency, Read_Latency => Read_Latency, Page_Latency => Page_Latency)
  port map (
    CLK          => CLK,
    Reset        => '0',
    CLK_150_Out  => pclk,
    Address      => s_addr,
    Write_Enable => s_wr,
    Write_Data   => s_wdata,
    Burst        => s_burst_i,
    Read_Enable  => s_rd,
    Read_Data    => s_rdata,
    Read_Valid   => s_rvalid,
    Busy         => open,
    Idle         => open,
    sdram_addr   => sdram_addr,
    sdram_ba     => sdram_ba,
    sdram_cas_n  => sdram_cas_n,
    sdram_cke    => sdram_cke,
    sdram_cs_n   => sdram_cs_n,
    sdram_dq     => sdram_dq,
    sdram_dqm    => sdram_dqm,
    sdram_ras_n  => sdram_ras_n,
    sdram_we_n   => sdram_we_n,
    sdram_clk    => sdram_clk
  );

end rtl;
