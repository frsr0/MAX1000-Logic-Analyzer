library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

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
  Rate_Div     : in  natural range 1 to 12000000 := 12;
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
    FAST_CLK     : in  std_logic := '0'
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
   signal s_busy  : std_logic := '0';
   signal s_burst_i : std_logic := '0';
   signal s_idle  : std_logic := '0';
  signal full_i  : std_logic := '0';

  -- Write FIFO (depth 16, 38-bit entries: addr(21:0) & wdata(15:0))
  constant FIFO_Depth : natural := 16;
  type fifo_array is array (0 to FIFO_Depth-1) of std_logic_vector(37 downto 0);
  signal fifo_mem  : fifo_array := (others => (others => '0'));
  signal fifo_cnt  : natural range 0 to FIFO_Depth := 0;

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

  CLK_150 <= pclk;

  -- Divider: assert sample_en for one cycle every Rate_Div PLL clocks
  process (pclk)
    variable cnt : natural range 0 to 12000000 := 0;
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

  -- Main: capture samples directly to SDRAM, read back from SDRAM
  process (pclk)
    variable step_r  : natural range 0 to sub_steps := 0;
    variable run_r   : std_logic := '0';
    variable rd_mode : boolean := true;
    variable wbuf    : std_logic_vector(15 downto 0) := (others => '0');
    variable waddr   : natural range 0 to 15000000 := 0;
    variable a_reg   : natural range 0 to 15000000 := 15000000;
    variable wip        : boolean := false;
    variable wr_last    : std_logic_vector(1 downto 0) := "00";
    variable wr_pend    : boolean := false;
    variable wr_pend_addr : std_logic_vector(21 downto 0) := (others => '0');
    variable wr_pend_data : std_logic_vector(15 downto 0) := (others => '0');
    variable rd_pend : std_logic := '0';
    variable f_head  : natural range 0 to FIFO_Depth-1 := 0;
    variable f_tail  : natural range 0 to FIFO_Depth-1 := 0;
    variable f_cnt   : natural range 0 to FIFO_Depth := 0;
    variable burst_rem   : natural range 0 to 4 := 0;
    variable burst_phase : boolean := false;
    type burst_buf_t is array(0 to 3) of std_logic_vector(37 downto 0);
    variable burst_buf   : burst_buf_t;
    variable bram_wp   : natural range 0 to BRAM_SIZE-1 := 0;
    variable bram_cnt  : natural range 0 to BRAM_SIZE := 0;
    variable bram_post_cnt : natural range 0 to BRAM_SIZE := 0;
    variable flush_rem   : natural range 0 to BRAM_SIZE := 0;
    variable flush_idx   : natural range 0 to BRAM_SIZE-1 := 0;
    variable flush_total : natural range 0 to BRAM_SIZE := 0;
    variable flush_sync : boolean := false;
  begin
    if rising_edge(pclk) then
      bram_wren <= '0';
      if Run /= run_r then
        waddr := 0; step_r := 0;
        if Run = '1' then full_i <= '0'; end if;  -- reset full on new capture start only
        if Run = '0' then
          rd_mode := true;
        elsif Fast_Mode = '1' then
          -- Fast mode trigger: continue BRAM circular buffer for post-trigger samples
          rd_mode := false;
          bram_post_cnt := 0;
        else
          rd_mode := false;
          -- Trigger fired: start flushing BRAM to FIFO (only if pre-trigger samples exist)
          if Armed = '1' and bram_cnt > 0 then
            flush_rem := bram_cnt;
            flush_total := bram_cnt;
            if bram_cnt < BRAM_SIZE then
              flush_idx := 0;  -- BRAM not wrapped, oldest at 0
            else
              flush_idx := bram_wp;  -- wrapped: oldest is at next write position
            end if;
            bram_raddr <= flush_idx;  -- prime first flush read
            flush_sync := false;     -- skip one cycle for BRAM read latency
          end if;
          bram_cnt := 0;
        end if;
        run_r := Run;
        s_wr <= '0'; s_rd <= '0';
      end if;

      -- Fast mode pre-trigger: enter capture mode when armed, not triggered
      if Fast_Mode = '1' and Armed = '1' and Run = '0' and rd_mode then
        rd_mode := false;
        bram_cnt := 0;
      end if;

      if rd_mode then
        -- READOUT
        s_wr <= '0'; s_burst_i <= '0';
        if Fast_Mode = '1' then
          -- Fast mode: read directly from BRAM (circular wrap aware)
          if Address /= a_reg then
            a_reg := Address;
            if bram_cnt + bram_post_cnt <= BRAM_SIZE then
              bram_raddr <= Address;  -- not wrapped
            else
              -- Wrapped: oldest data is at bram_wp (next write position)
              bram_raddr <= (bram_wp + Address) mod BRAM_SIZE;
            end if;
          end if;
          if Address < bram_cnt + bram_post_cnt then
            Outputs <= bram_rdata;
          else
            Outputs <= (others => '0');
          end if;
        else
          -- Normal mode: read from SDRAM
          if Address /= a_reg then
            a_reg := Address;
            s_addr <= std_logic_vector(to_unsigned(Address, 22));
            s_rd <= '1';
            rd_pend := '1';
          end if;
          if s_rvalid = '1' and rd_pend = '1' then
            Outputs <= s_rdata;
            s_rd <= '0';
            rd_pend := '0';
          end if;
        end if;

      else
        -- CAPTURE

        -- Write pump: three mutually exclusive paths.
        -- Path 1 (highest priority): burst in progress
        if burst_rem > 0 then
          s_burst_i <= '1';
          if not burst_phase then
            -- Drive s_wr high with current burst entry
            s_addr  <= burst_buf(4 - burst_rem)(37 downto 16);
            s_wdata <= burst_buf(4 - burst_rem)(15 downto 0);
            s_wr    <= '1';
            burst_phase := true;
          else
            -- Drive s_wr low: creates falling edge for controller edge detect
            s_wr    <= '0';
            burst_phase := false;
            burst_rem := burst_rem - 1;
            if burst_rem = 0 then
              wip    := true;
              s_burst_i <= '0';
            end if;
          end if;

        -- Path 2: single write pending
        elsif wr_pend then
          s_addr  <= wr_pend_addr;
          s_wdata <= wr_pend_data;
          s_wr    <= '1';
          wip     := true;
          wr_pend := false;

        -- Path 3: pop from FIFO
        elsif f_cnt > 0 and not wip then
          if f_cnt >= 4 then
            -- Pop 4 entries into burst buffer
            for i in 0 to 3 loop
              burst_buf(i) := fifo_mem(f_tail);
              if f_tail = FIFO_Depth-1 then f_tail := 0;
              else f_tail := f_tail + 1; end if;
              f_cnt := f_cnt - 1;
            end loop;
            burst_rem   := 4;
            burst_phase := false;
            s_burst_i     <= '1';
            -- Drive first entry now; next cycle burst_rem>0 starts toggling
            s_addr  <= burst_buf(0)(37 downto 16);
            s_wdata <= burst_buf(0)(15 downto 0);
            s_wr    <= '1';
          else
            -- Single write (unchanged)
            wr_pend      := true;
            wr_pend_addr := fifo_mem(f_tail)(37 downto 16);
            wr_pend_data := fifo_mem(f_tail)(15 downto 0);
            if f_tail = FIFO_Depth-1 then f_tail := 0;
            else f_tail := f_tail + 1; end if;
            f_cnt := f_cnt - 1;
          end if;
        end if;

        -- Track in-progress SDRAM write completion (falling edge of s_busy)
        if wip then
          wr_last := wr_last(0) & s_busy;
          if wr_last = "10" then
            s_wr <= '0'; wip := false; wr_last := "00";
          end if;
        end if;

        -- Sample new data when tick arrives; write to BRAM (pre-trigger) or FIFO (post-trigger)
        if sample_en = '1' then
          wbuf(((step_r + 1) * Channels) - 1 downto step_r * Channels) := Inputs;

          if step_r = sub_steps - 1 then
            -- Full 16-bit word ready
            if flush_rem > 0 then
              -- Flush BRAM to FIFO (pre-trigger samples flushed after trigger)
              if f_cnt < FIFO_Depth then
                if flush_sync then
                  fifo_mem(f_head) <= std_logic_vector(to_unsigned(waddr, 22)) & bram_rdata;
                  if f_head = FIFO_Depth-1 then f_head := 0;
                  else f_head := f_head + 1; end if;
                  f_cnt := f_cnt + 1;
                  waddr := waddr + 1;
                  flush_rem := flush_rem - 1;
                end if;
                flush_sync := true;
                if flush_idx = BRAM_SIZE-1 then flush_idx := 0;
                else flush_idx := flush_idx + 1; end if;
                bram_raddr <= flush_idx;  -- prime next flush read
              end if;
            elsif Armed = '1' and Run = '0' then
              -- Pre-trigger: store in BRAM (circular buffer)
              bram_waddr <= bram_wp;
              bram_wdata <= wbuf;
              bram_wren <= '1';
              if bram_wp = BRAM_SIZE-1 then bram_wp := 0;
              else bram_wp := bram_wp + 1; end if;
              if bram_cnt < BRAM_SIZE then bram_cnt := bram_cnt + 1; end if;
            elsif Fast_Mode = '1' and Armed = '1' then
              -- Fast mode post-trigger: continue BRAM circular buffer
              bram_waddr <= bram_wp;
              bram_wdata <= wbuf;
              bram_wren <= '1';
              if bram_wp = BRAM_SIZE-1 then bram_wp := 0;
              else bram_wp := bram_wp + 1; end if;
              if bram_cnt < BRAM_SIZE then bram_cnt := bram_cnt + 1; end if;
              bram_post_cnt := bram_post_cnt + 1;
            elsif f_cnt < FIFO_Depth then
              -- Post-trigger: push captured word to FIFO
              fifo_mem(f_head) <= std_logic_vector(to_unsigned(waddr, 22)) & wbuf;
              if f_head = FIFO_Depth-1 then f_head := 0;
              else f_head := f_head + 1; end if;
              f_cnt := f_cnt + 1;
              waddr := waddr + 1;
            end if;
          end if;

          if step_r = sub_steps - 1 then step_r := 0;
          else step_r := step_r + 1;
          end if;
        end if;

        -- Assert Full: normal mode (SDRAM) vs fast mode (BRAM only)
        if not rd_mode and full_i = '0' then
          if Fast_Mode = '1' then
            -- Fast mode: capture BRAM_SIZE post-trigger words (fills circular buffer)
            if bram_post_cnt >= BRAM_SIZE then
              full_i <= '1';
              rd_mode := true;
            end if;
          elsif waddr >= (Samples / sub_steps) + flush_total
             and f_cnt = 0
             and not wip
             and not wr_pend
             and burst_rem = 0
          then
            full_i <= '1';
            rd_mode := true;
          end if;
        end if;
      end if;

      -- Drive fifo_cnt signal from variable for external visibility
      fifo_cnt <= f_cnt;

      -- Status(3 downto 0): run_r, wip, s_rd, full_i
      Status(0) <= run_r;
      if wip then Status(1) <= '1'; else Status(1) <= '0'; end if;
      Status(2) <= s_rd;
      Status(3) <= full_i;
      -- Status(7 downto 4): fifo_cnt binary (4 bits, 0-15)
      if    f_cnt >= 8 then Status(7) <= '1'; else Status(7) <= '0'; end if;
      if    f_cnt = 4 or f_cnt = 5 or f_cnt = 6 or f_cnt = 7
         or f_cnt = 12 or f_cnt = 13 or f_cnt = 14 or f_cnt = 15
         then Status(6) <= '1'; else Status(6) <= '0'; end if;
      if    f_cnt = 2 or f_cnt = 3 or f_cnt = 6 or f_cnt = 7
         or f_cnt = 10 or f_cnt = 11 or f_cnt = 14 or f_cnt = 15
         then Status(5) <= '1'; else Status(5) <= '0'; end if;
      if    f_cnt = 1 or f_cnt = 3 or f_cnt = 5 or f_cnt = 7
         or f_cnt = 9 or f_cnt = 11 or f_cnt = 13 or f_cnt = 15
         then Status(4) <= '1'; else Status(4) <= '0'; end if;
    end if;
  end process;

  Full <= full_i;
  s_burst <= s_burst_i;

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
    Busy         => s_busy,
    Idle         => s_idle,
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
