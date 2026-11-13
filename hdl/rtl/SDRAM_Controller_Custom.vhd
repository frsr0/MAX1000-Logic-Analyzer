library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SDRAM_Controller is
  generic (
    CLK_Frequency : natural := 96000000  -- Hz
  );
port (
    sdram_addr  : out std_logic_vector(11 downto 0);
    sdram_ba    : out std_logic_vector(1 downto 0);
    sdram_cas_n : out std_logic;
    sdram_cke   : out std_logic;
    sdram_cs_n  : out std_logic;
    sdram_dq    : inout std_logic_vector(15 downto 0);
    sdram_dqm   : out std_logic_vector(1 downto 0);
    sdram_ras_n : out std_logic;
    sdram_we_n  : out std_logic;

    sdram_s_address       : in std_logic_vector(21 downto 0);
    sdram_s_byteenable_n  : in std_logic_vector(1 downto 0);
    sdram_s_chipselect    : in std_logic;
    sdram_s_writedata     : in std_logic_vector(15 downto 0);
    sdram_s_read_n        : in std_logic;
    sdram_s_write_n       : in std_logic;
    sdram_s_readdata      : out std_logic_vector(15 downto 0);
    sdram_s_readdatavalid : out std_logic;
    sdram_s_waitrequest   : out std_logic;
    sdram_s_idle          : out std_logic;
    -- Pull-based capture burst interface (see the block comment below the
    -- architecture declarations for the full protocol).
    capture_burst_req     : in std_logic := '0';
    capture_burst_addr    : in std_logic_vector(21 downto 0) := (others => '0');
    capture_burst_len     : in std_logic_vector(8 downto 0) := (others => '0');
    capture_burst_stop    : in std_logic := '0';
    capture_pop_ok        : in std_logic := '0';
    capture_data          : in std_logic_vector(15 downto 0) := (others => '0');
    capture_pop           : out std_logic;
    capture_wr            : out std_logic;
    capture_busy          : out std_logic;

    reset_reset_n         : in std_logic;
    clk_in_clk            : in std_logic
);
end SDRAM_Controller;

architecture rtl of SDRAM_Controller is

    function to_hex(slv : std_logic_vector) return string is
        variable result : string(1 to (slv'length+3)/4);
        variable nibble : std_logic_vector(3 downto 0);
        variable n : integer;
    begin
        for i in result'reverse_range loop
            n := i * 4;
            if n+3 < slv'length then
                nibble := slv(n+3 downto n);
            else
                nibble := (others => '0');
                nibble(slv'length-n-1 downto 0) := slv(slv'length-1 downto n);
            end if;
            case nibble is
                when "0000" => result(i) := '0';
                when "0001" => result(i) := '1';
                when "0010" => result(i) := '2';
                when "0011" => result(i) := '3';
                when "0100" => result(i) := '4';
                when "0101" => result(i) := '5';
                when "0110" => result(i) := '6';
                when "0111" => result(i) := '7';
                when "1000" => result(i) := '8';
                when "1001" => result(i) := '9';
                when "1010" => result(i) := 'A';
                when "1011" => result(i) := 'B';
                when "1100" => result(i) := 'C';
                when "1101" => result(i) := 'D';
                when "1110" => result(i) := 'E';
                when "1111" => result(i) := 'F';
                when others => result(i) := 'X';
            end case;
        end loop;
        return result;
    end function;

    function cycles_for_ns(ns : real; clk_hz : natural) return natural is
        variable r : real;
    begin
        r := ns * real(clk_hz) / 1.0e9;
        if r = real(integer(r)) then
            return integer(r);
        else
            return integer(r) + 1;
        end if;
    end function;

    -- SDRAM timing parameters (nanoseconds). These are the proven values: the
    -- prime/drain block readout uses a FIXED 3-cycle latch tuned to the
    -- resulting controller read latency, so changing tRCD/tRP/tRFC misaligns it
    -- and corrupts reads everywhere (verified: padding them broke the readout).
    -- (Padding was tried to fix the boundary residual and made no difference to
    -- it, confirming that residual is elsewhere in the controller read path.)
    constant T_RCD  : real := 20.0;
    constant T_WR   : real := 15.0;
    constant T_RP   : real := 15.0;
    constant T_RFC  : real := 70.0;
    constant T_MRD  : natural := 2;

    -- Calculated counter limits
    constant TRCD_CYCLES : natural := cycles_for_ns(T_RCD, CLK_Frequency);
    constant TWR_CYCLES  : natural := cycles_for_ns(T_WR,  CLK_Frequency);
    constant TRP_CYCLES  : natural := cycles_for_ns(T_RP,  CLK_Frequency);
    constant TRFC_CYCLES : natural := cycles_for_ns(T_RFC, CLK_Frequency);

    -- Refresh timing: need 8192 refreshes per 64ms → one every 7.8125us
    constant REF_CYCLES  : natural := cycles_for_ns(7812.5, CLK_Frequency);

    type state_type is (
        ST_INIT, ST_INIT_NOP,
        ST_PRE, ST_PRE_WAIT,
        ST_REF1, ST_REF1_WAIT, ST_REF2, ST_REF2_WAIT,
        ST_MRS, ST_MRS_WAIT,
        ST_IDLE,
        ST_ACT, ST_TRCD,
        ST_RD, ST_CL_WAIT, ST_RD_DATA,
        ST_WR, ST_TWR,
        ST_STREAM_WR, ST_BPEND,
        ST_PRE2, ST_TRP2,
        ST_RFSH_PRE, ST_RFSH, ST_TRFC,
        ST_DEASSERT
    );
    signal state : state_type := ST_INIT;

    -- Runtime wait counter: only ever counts to small values (tRCD/tWR/tRFC/tRP/
    -- CL, all < 16), so a narrow range keeps its carry chain short at 167 MHz.
    -- The one-time ~100 us power-up wait uses init_cnt instead, so the wide 19999
    -- compare/increment is off this (per-operation, timing-critical) counter.
    signal cnt   : integer range 0 to 63 := 0;
    signal init_cnt : integer range 0 to 32767 := 0;  -- ST_INIT power-up wait only
    signal init_cnt_done : std_logic := '0';  -- registered init_cnt >= 19998
    -- The refresh timer never exceeds REF_CYCLES-1: the registered compare
    -- clears it on the first cycle at the terminal count, and the refresh
    -- precharge delay only loads TRP_CYCLES-1.  Constraining the range lets
    -- Quartus implement an 11-bit counter (for the 1308-cycle 167 MHz
    -- refresh interval) instead of a 16-bit adder on the SDRAM clock path.
    signal timer : integer range 0 to REF_CYCLES - 1 := 0;
    -- Registered versions of wide comparator outputs to reduce combinational depth
    signal timer_ge_ref   : std_logic := '0';  -- timer >= REF_CYCLES - 1
    signal timer_eq_zero  : std_logic := '0';  -- timer = 0

    signal ref_req : std_logic := '0';

    signal buf_a : std_logic_vector(21 downto 0) := (others => '0');
    signal buf_wd : std_logic_vector(15 downto 0) := (others => '0');
    signal buf_a_next : std_logic_vector(21 downto 0) := (others => '0');
    signal buf_wd_next : std_logic_vector(15 downto 0) := (others => '0');
    signal is_read : std_logic := '0';

    signal last_rn : std_logic := '1';
    signal last_wn : std_logic := '1';
    signal pend_rn : std_logic := '0';
    signal pend_wn : std_logic := '0';
    signal pend_wn_next : std_logic := '0';
    signal pend_wn_same_row : std_logic := '0';
    signal pend_wn_next_same_row : std_logic := '0';

    signal dq_oe : std_logic := '0';
    -- Registered streaming write data (see DQ mux comment).
    -- Single registered DQ output. Drives the sdram_dq pin directly (no
    -- combinational mux), so it packs into the I/O cell and the write data has a
    -- clean, glitch-free register->pin path. Loaded at every write-command cycle
    -- (ST_STREAM_WR streaming write, ST_WR burst/avalon write).
    signal dq_out : std_logic_vector(15 downto 0) := (others => '0');

    signal bank_r : std_logic_vector(1 downto 0) := "00";
    signal row_r  : std_logic_vector(11 downto 0) := (others => '0');
    signal col_r  : std_logic_vector(7 downto 0) := (others => '0');

    signal s_cs  : std_logic := '1';
    signal s_ras : std_logic := '1';
    signal s_cas : std_logic := '1';
    signal s_we  : std_logic := '1';
    signal s_addr : std_logic_vector(11 downto 0) := (others => '0');
    signal s_ba   : std_logic_vector(1 downto 0) := (others => '0');
    signal s_cs_r   : std_logic := '1';
    signal s_ras_r  : std_logic := '1';
    signal s_cas_r  : std_logic := '1';
    signal s_we_r   : std_logic := '1';
    signal s_ba_r   : std_logic_vector(1 downto 0) := (others => '0');
    signal s_addr_r : std_logic_vector(11 downto 0) := (others => '0');

    signal write_depth : natural := 0;
    signal max_write_depth : natural := 0;

    -- Page-mode: track open row
    signal active_row  : std_logic_vector(11 downto 0) := (others => '0');
    signal active_bank : std_logic_vector(1 downto 0) := (others => '0');
    signal row_open    : std_logic := '0';
    signal last_op_was_stream : std_logic := '0';
    -- Registered is_same_row comparator outputs decouple 14-bit address
    -- comparisons from command/address output timing.
    signal same_row_buf_a      : std_logic := '0';
    signal same_row_buf_next   : std_logic := '0';
    signal same_row_burst      : std_logic := '0';

    -- ---------------------------------------------------------------------
    -- Capture burst (pull) interface.
    --   Pump -> controller (all registered in the pump):
    --     capture_burst_req   request level; held until capture_busy rises
    --     capture_burst_addr  start address (bank/row/col); stable from req
    --                         until capture_busy falls again
    --     capture_burst_len   1..256 words; never crosses the 256-column row
    --     capture_burst_stop  abort the burst (no new pops, in-flight words
    --                         are still written)
    --     capture_pop_ok      the FIFO holds at least one word that has not been
    --                         popped yet (the pump accounts for words in flight)
    --     capture_data        FIFO q: the word popped by capture_pop is on it
    --                         ONE cycle after the pop
    --   Controller -> pump (registered):
    --     capture_pop         FIFO read request (one word per asserted cycle)
    --     capture_wr          a popped word is being written this cycle
    --     capture_busy        a burst is latched / running / draining
    --   Pop at cycle c -> data on capture_data at c+1 (wv) -> WRITE command is
    --   decided at c+1, reaches the pins at c+3 together with dq_out (d1 is
    --   the extra register that aligns the data with the registered command).
    -- ---------------------------------------------------------------------
    signal bp          : std_logic := '0';  -- burst latched, row not yet ready
    signal busy_r      : std_logic := '0';
    signal in_stream   : std_logic := '0';  -- mirror of state = ST_STREAM_WR
    signal pop_r       : std_logic := '0';
    signal wv          : std_logic := '0';  -- popped word is on capture_data
    signal d1          : std_logic_vector(15 downto 0) := (others => '0');
    signal d1_v        : std_logic := '0';
    signal brem        : unsigned(8 downto 0) := (others => '0');
    signal brem_nz     : std_logic := '0';

    -- Mode register: A2:0 burst length 1, A3 sequential, A6:4 CAS latency,
    -- A9:7 standard operating mode. CAS latency is "011" (CL3): at 167 MHz (6 ns)
    -- CL2's DQ-valid window is too tight for the worst-case first read after the
    -- bus idled across the inter-block gap, which read back 0xFFFF (un-driven DQ)
    -- -> the deterministic block-boundary readback corruption. CL3 gives the chip
    -- an extra cycle to drive DQ. The read FSM matches it: the READ command
    -- reaches the pins two cycles after ST_RD (the s_cas_r register stage), so
    -- ST_CL_WAIT holds for 2 + RD_WAIT_EXTRA_CYCLES cycles and ST_RD_DATA
    -- samples DQ inside the chip's CL3 data window.
    constant MR : std_logic_vector(11 downto 0) := "000000110000";

    -- Extra sdram_core_clk cycles in ST_CL_WAIT, on top of the two that match
    -- CL3. The command reaches the pins two cycles after ST_RD and a real CL3
    -- part drives DQ tAC after the CL-th pin clock edge, so the latch must be
    -- placed from the pin-level command, not from ST_RD. 0 reproduces the
    -- original latch (which sampled the previous read's data on the board);
    -- the board-validated value is substituted here.
    constant RD_WAIT_EXTRA_CYCLES : natural := 3;

    function is_same_row(addr : std_logic_vector(21 downto 0);
                         row  : std_logic_vector(11 downto 0);
                         bank : std_logic_vector(1 downto 0)) return boolean is
    begin
        return addr(19 downto 8) = row and addr(21 downto 20) = bank;
    end function;

begin

    sdram_cke <= '1';
    sdram_dqm <= "00";

    sdram_cs_n <= s_cs_r;
    sdram_ras_n <= s_ras_r;
    sdram_cas_n <= s_cas_r;
    sdram_we_n  <= s_we_r;
    sdram_addr  <= s_addr_r;
    sdram_ba    <= s_ba_r;

    -- DQ pin driven by a SINGLE registered value (dq_out) gated by a registered
    -- output-enable (dq_oe). OE is asserted only in the actual write-command
    -- states, keeping row/page-hit decisions out of the IO OE timing path.
    sdram_dq <= dq_out when dq_oe = '1' else (others => 'Z');
    -- Registered comparator outputs (concurrent assignments resolve one delta
    -- after timer updates, effectively pipelining the wide comparisons away from
    -- the state machine decision cone and ref_req driver.)
    timer_ge_ref <= '1' when timer >= REF_CYCLES - 1 else '0';
    timer_eq_zero <= '1' when timer = 0 else '0';

    sdram_s_idle <= '1' when state = ST_IDLE else '0';
    capture_pop  <= pop_r;
    capture_wr   <= wv;
    capture_busy <= busy_r;

    -- synthesis translate_off
    process(max_write_depth)
    begin
        if now > 0 fs then
            report "max_write_depth=" & integer'image(max_write_depth) severity note;
        end if;
    end process;
    -- synthesis translate_on

    process(clk_in_clk, reset_reset_n)
        variable v_depth : natural := 0;
        variable v_peak : natural := 0;
    begin
        if reset_reset_n = '0' then
            state <= ST_INIT;
            cnt <= 0; init_cnt <= 0; init_cnt_done <= '0'; timer <= 0; ref_req <= '0';
            dq_oe <= '0';
            sdram_s_waitrequest <= '1'; sdram_s_readdatavalid <= '0';
            sdram_s_readdata <= (others => '0');
            last_rn <= '1'; last_wn <= '1';
            pend_rn <= '0'; pend_wn <= '0'; pend_wn_next <= '0';
            pend_wn_same_row <= '0'; pend_wn_next_same_row <= '0';
            buf_a <= (others => '0'); buf_wd <= (others => '0');
            dq_out <= (others => '0');
            buf_a_next <= (others => '0'); buf_wd_next <= (others => '0');
            is_read <= '0';
            s_cs <= '1'; s_ras <= '1'; s_cas <= '1'; s_we <= '1';
            s_addr <= (others => '0'); s_ba <= (others => '0');
            s_cs_r   <= '1';
            s_ras_r  <= '1';
            s_cas_r  <= '1';
            s_we_r   <= '1';
            s_ba_r   <= (others => '0');
            s_addr_r <= (others => '0');
            write_depth <= 0; max_write_depth <= 0;
            row_open <= '0'; active_row <= (others => '0'); active_bank <= (others => '0');
            same_row_buf_a <= '0'; same_row_buf_next <= '0'; same_row_burst <= '0';
            bp <= '0'; busy_r <= '0'; in_stream <= '0'; pop_r <= '0'; wv <= '0';
            d1 <= (others => '0'); d1_v <= '0';
            brem <= (others => '0'); brem_nz <= '0';

            last_op_was_stream <= '0';

        elsif rising_edge(clk_in_clk) then

            last_rn <= sdram_s_read_n;
            last_wn <= sdram_s_write_n;
            if is_same_row(buf_a, active_row, active_bank) then
                same_row_buf_a <= '1';
            else
                same_row_buf_a <= '0';
            end if;
            if is_same_row(buf_a_next, active_row, active_bank) then
                same_row_buf_next <= '1';
            else
                same_row_buf_next <= '0';
            end if;
            if is_same_row(capture_burst_addr, active_row, active_bank) then
                same_row_burst <= '1';
            else
                same_row_burst <= '0';
            end if;

            -- Refresh timer
            -- Refresh timer: use registered timer_ge_ref (computed concurrently)
            -- instead of the raw 16-bit comparator to decouple it from the
            -- state machine decision MUX.
            if timer_ge_ref = '1' then
                ref_req <= '1';
                timer <= 0;
            else
                timer <= timer + 1;
            end if;

            -- Capture burst datapath (see the interface comment above).
            -- A popped word returns on capture_data one cycle after the pop.
            -- d1 is a free-running copy; dq_out takes it when the word is
            -- valid so the data reaches the pins in the same cycle as the
            -- registered WRITE command (command: decide at wv, s_*, s_*_r;
            -- data: d1, dq_out -> both visible two cycles after wv).
            wv   <= pop_r;
            d1   <= capture_data;
            d1_v <= wv;
            if d1_v = '1' then
                dq_out <= d1;
            end if;
            -- Pop control: every term is a register (no FIFO flag logic here;
            -- capture_pop_ok is the pump's registered permission).
            if in_stream = '1' and brem_nz = '1' and capture_pop_ok = '1'
               and ref_req = '0' and pend_rn = '0' and capture_burst_stop = '0' then
                pop_r <= '1';
                brem  <= brem - 1;
                if brem = 1 then
                    brem_nz <= '0';
                end if;
            else
                pop_r <= '0';
            end if;

            v_depth := write_depth;

            -- Edge detection: latch request on falling edge of read_n/write_n
            if last_rn = '1' and sdram_s_read_n = '0' then
                if pend_rn = '0' and pend_wn = '0' then
                    buf_a <= sdram_s_address;
                end if;
                pend_rn <= '1';
                pend_wn <= '0';
            elsif last_wn = '1' and sdram_s_write_n = '0' then
                    -- Capture uses capture_stream_* exclusively. Keep the simple
                    -- single-write Avalon path for legacy/sim access.
                    v_depth := v_depth + 1;
                    if pend_wn = '0' then
                    buf_a <= sdram_s_address;
                    buf_wd <= sdram_s_writedata;
                    pend_wn <= '1';
                else
                    buf_a_next <= sdram_s_address;
                    buf_wd_next <= sdram_s_writedata;
                    pend_wn_next <= '1';
                end if;
                pend_rn <= '0';
            end if;

            v_peak := v_depth;

            s_cs <= '0'; s_ras <= '1'; s_cas <= '1'; s_we <= '1';
            s_addr <= "0000" & col_r;
            s_ba <= active_bank;

            case state is

                -- INITIALIZATION
                when ST_INIT =>
                    s_cs <= '1';
                    -- Wide 15-bit compare registered (init_cnt_done) so the
                    -- state transition is a 1-FF path, not a live counter
                    -- decode (was the worst sdram_core_clk setup cone).
                    if init_cnt >= 19998 then
                        init_cnt_done <= '1';
                    else
                        init_cnt_done <= '0';
                    end if;
                    if init_cnt < 19999 then
                        init_cnt <= init_cnt + 1;
                    else
                        init_cnt <= 0;
                    end if;
                    if init_cnt_done = '1' then
                        state <= ST_INIT_NOP;
                    end if;

                when ST_INIT_NOP =>
                    state <= ST_PRE;

                when ST_PRE =>
                    s_ras <= '0'; s_we <= '0';
                    s_addr(10) <= '1'; s_ba <= "00";
                    state <= ST_PRE_WAIT;

                when ST_PRE_WAIT =>
                    if cnt < 2 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_REF1;
                    end if;

                when ST_REF1 =>
                    s_ras <= '0'; s_cas <= '0';
                    state <= ST_REF1_WAIT;

                when ST_REF1_WAIT =>
                    if cnt < 7 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_REF2;
                    end if;

                when ST_REF2 =>
                    s_ras <= '0'; s_cas <= '0';
                    state <= ST_REF2_WAIT;

                when ST_REF2_WAIT =>
                    if cnt < 7 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_MRS;
                    end if;

                when ST_MRS =>
                    s_ras <= '0'; s_cas <= '0'; s_we <= '0';
                    s_addr <= MR; s_ba <= "00";
                    state <= ST_MRS_WAIT;

                when ST_MRS_WAIT =>
                    if cnt < 2 then cnt <= cnt + 1;
                    else cnt <= 0;
                        sdram_s_waitrequest <= '0';
                        state <= ST_IDLE;
                    end if;

                -- IDLE: wait for requests. Latch address when processing.
                when ST_IDLE =>
                    if ref_req = '1' and pend_rn = '0' and pend_wn = '0' and pend_wn_next = '0' then
                        ref_req <= '0';
                        sdram_s_waitrequest <= '1';
                        if row_open = '1' then
                            -- Precharge before refresh (tRP requirement)
                            s_ras <= '0'; s_we <= '0';
                            s_addr(10) <= '1'; s_ba <= active_bank;
                            row_open <= '0';
                            timer <= TRP_CYCLES - 1;
                            state <= ST_RFSH_PRE;
                        else
                            state <= ST_RFSH;
                        end if;
                    elsif pend_rn = '1' then
                        is_read <= '1';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        if row_open = '1' then
                            -- Another row is open: precharge first, then
                            -- service this read from ST_DEASSERT (keep pend_rn
                            -- asserted across the precharge so it is not lost).
                            state <= ST_PRE2;
                        else
                            pend_rn <= '0';
                            state <= ST_ACT;
                        end if;
                    elsif pend_wn = '1' then
                        pend_wn <= '0';
                        is_read <= '0';
                        last_op_was_stream <= '0';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if pend_wn_next = '1' then
                            buf_a <= buf_a_next;
                            buf_wd <= buf_wd_next;
                            pend_wn <= '1';
                            pend_wn_next <= '0';
                        end if;
                        -- Page-mode: if same row is open, skip activate
                        if row_open = '1' and same_row_buf_a = '1' then
                            pend_wn_same_row <= '1';
                            state <= ST_WR;
                        else
                            pend_wn_same_row <= '0';
                            state <= ST_ACT;
                        end if;
                    elsif pend_wn_next = '1' then
                        bank_r <= buf_a_next(21 downto 20); row_r <= buf_a_next(19 downto 8); col_r <= buf_a_next(7 downto 0);
                        buf_a <= buf_a_next;
                        buf_wd <= buf_wd_next;
                        pend_wn <= '1';
                        pend_wn_next <= '0';
                        is_read <= '0';
                        last_op_was_stream <= '0';
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if row_open = '1' and same_row_buf_next = '1' then
                            pend_wn_same_row <= '1';
                            state <= ST_WR;
                        else
                            pend_wn_same_row <= '0';
                            state <= ST_ACT;
                        end if;
                    elsif bp = '1' or (capture_burst_req = '1' and capture_burst_stop = '0') then
                        -- Capture burst: latch the request (the pump holds the
                        -- address/length stable until busy falls) and let
                        -- ST_BPEND pick activate / precharge / straight-in.
                        bp <= '1'; busy_r <= '1'; cnt <= 0;
                        sdram_s_waitrequest <= '1';
                        state <= ST_BPEND;
                    end if;

                -- READ/WRITE SEQUENCE
                when ST_ACT =>
                    s_ras <= '0';
                    s_addr <= row_r; s_ba <= bank_r;
                    active_row <= row_r; active_bank <= bank_r; row_open <= '1';
                    state <= ST_TRCD;

                when ST_TRCD =>
                    if cnt < TRCD_CYCLES - 1 then cnt <= cnt + 1;
                    else cnt <= 0;
                        if is_read = '1' then
                            state <= ST_RD;
                        elsif bp = '1' then
                            in_stream <= '1';
                            state <= ST_STREAM_WR;
                        else
                            state <= ST_WR;
                        end if;
                    end if;

                when ST_RD =>
                    s_cas <= '0';
                    state <= ST_CL_WAIT;

                when ST_CL_WAIT =>
                    -- CL3: the READ command reaches the pins two cycles after
                    -- ST_RD (s_cas_r), and the chip drives DQ tAC after the
                    -- CL-th pin clock edge. Hold 2 + RD_WAIT_EXTRA_CYCLES cycles
                    -- so ST_RD_DATA samples DQ inside that data window.
                    if cnt < 2 + RD_WAIT_EXTRA_CYCLES then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_RD_DATA;
                    end if;

                when ST_RD_DATA =>
                    sdram_s_readdata <= sdram_dq;
                    sdram_s_readdatavalid <= '1';
                    state <= ST_PRE2;

                when ST_WR =>
                    s_cas <= '0'; s_we <= '0';
                    dq_oe <= '1';
                    last_op_was_stream <= '0';
                    -- Drive DQ from the single registered output, aligned with the
                    -- write command.
                    dq_out <= buf_wd;
                    state <= ST_TWR;

                when ST_BPEND =>
                    -- Burst latched. Two cycles let same_row_burst (registered
                    -- compare of the burst address against the open row) settle,
                    -- then: row closed -> ACT; same row open -> straight in;
                    -- other row open -> precharge first (re-enters via DEASSERT).
                    if capture_burst_stop = '1' then
                        bp <= '0'; busy_r <= '0'; cnt <= 0;
                        state <= ST_DEASSERT;
                    elsif ref_req = '1' or pend_rn = '1' then
                        -- Let refresh / a pending read go first; bp stays set.
                        cnt <= 0;
                        state <= ST_DEASSERT;
                    elsif cnt < 1 then
                        cnt <= cnt + 1;
                    else
                        cnt <= 0;
                        is_read <= '0';
                        last_op_was_stream <= '1';
                        bank_r <= capture_burst_addr(21 downto 20);
                        row_r  <= capture_burst_addr(19 downto 8);
                        col_r  <= capture_burst_addr(7 downto 0);
                        brem   <= unsigned(capture_burst_len);
                        if unsigned(capture_burst_len) = 0 then
                            brem_nz <= '0';
                        else
                            brem_nz <= '1';
                        end if;
                        if row_open = '0' then
                            state <= ST_ACT;
                        elsif same_row_burst = '1' then
                            in_stream <= '1';
                            state <= ST_STREAM_WR;
                        else
                            state <= ST_PRE2;
                        end if;
                    end if;

                when ST_STREAM_WR =>
                    last_op_was_stream <= '1';
                    bp <= '0';
                    -- One WRITE per valid word, column from col_r (loaded at
                    -- burst start, +1 per write; s_addr defaults to col_r).
                    if wv = '1' then
                        dq_oe <= '1';
                        s_cas <= '0'; s_we <= '0';
                        col_r <= std_logic_vector(unsigned(col_r) + 1);
                    end if;
                    -- End of burst: length used up, or an early stop (refresh,
                    -- pending read, pump abort) -- but only once nothing is in
                    -- flight (no pop this cycle, none returning), so no popped
                    -- word is ever left unwritten.
                    if (brem_nz = '0' or ref_req = '1' or pend_rn = '1'
                        or capture_burst_stop = '1')
                       and pop_r = '0' and wv = '0' then
                        busy_r <= '0'; in_stream <= '0';
                        state <= ST_TWR;
                    end if;

                when ST_TWR =>
                    -- Command/address pins pass through the s_*_r register bank,
                    -- so the WRITE selected in ST_WR/ST_STREAM_WR reaches the
                    -- SDRAM pins one clock after dq_oe is asserted.  Keep DQ
                    -- driven through that pin-level command cycle; releasing it
                    -- here used to make the chip sample Z for single writes.
                    -- For page-mode writes (same row pending), skip TWR delay.
                    -- tWR is only needed before precharge, not between same-row writes.
                    if last_op_was_stream = '0'
                    and ((pend_wn = '1' and pend_wn_same_row = '1')
                    or (pend_wn_next = '1' and pend_wn_next_same_row = '1')) then
                        cnt <= 0; state <= ST_DEASSERT;
                    elsif cnt < TWR_CYCLES - 1 then cnt <= cnt + 1;
                    else
                        cnt <= 0;
                        dq_oe <= '0';
                        state <= ST_DEASSERT;
                    end if;

                when ST_PRE2 =>
                    sdram_s_readdatavalid <= '0';
                    s_ras <= '0'; s_we <= '0';
                    s_addr(10) <= '1'; s_ba <= active_bank;
                    row_open <= '0';
                    state <= ST_TRP2;

                when ST_TRP2 =>
                    if cnt < TRP_CYCLES - 1 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_DEASSERT;
                    end if;

                -- REFRESH
                when ST_RFSH_PRE =>
                    if timer_eq_zero = '1' then
                        state <= ST_RFSH;
                    else
                        timer <= timer - 1;
                    end if;

                when ST_RFSH =>
                    s_ras <= '0'; s_cas <= '0';
                    row_open <= '0';
                    state <= ST_TRFC;

                when ST_TRFC =>
                    if cnt < TRFC_CYCLES - 1 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_DEASSERT;
                    end if;

                -- DONE: service next pending request, or go idle
                when ST_DEASSERT =>
                    -- Also releases DQ after the same-row fast path above.  At
                    -- this point the registered command pins have returned to
                    -- NOP, so data and output-enable remain aligned.
                    dq_oe <= '0';
                    sdram_s_waitrequest <= '0';
                    if ref_req = '1' and pend_rn = '0' and pend_wn = '0' and pend_wn_next = '0' then
                        sdram_s_waitrequest <= '1';
                        if row_open = '1' then
                            -- Close the open row first and KEEP ref_req set:
                            -- ST_PRE2 -> ST_TRP2 -> ST_DEASSERT re-runs this
                            -- arm with row_open = 0 and refreshes. (Clearing
                            -- ref_req here dropped the refresh whenever a
                            -- capture burst was cut short by it.)
                            state <= ST_PRE2;
                        else
                            ref_req <= '0';
                            state <= ST_RFSH;
                        end if;
                    elsif pend_wn = '1' and row_open = '1'
                       and pend_wn_same_row = '0' then
                        -- Cross-row write: close the open page-mode row BEFORE
                        -- activating the new one (the pend_rn read path below
                        -- already does this). Every same-row page-mode write
                        -- reuses the row activated in ST_ACT and only updates the
                        -- column; a write whose address crossed a 256-column page
                        -- boundary would otherwise activate the new row WITHOUT
                        -- precharging the old, so the sample lands in the stale
                        -- row -> the deterministic 256-sample-boundary readback
                        -- glitch. pend_wn stays set; ST_PRE2 -> ST_TRP2 ->
                        -- ST_DEASSERT re-runs this write with row_open=0 -> ST_ACT.
                        sdram_s_waitrequest <= '1';
                        state <= ST_PRE2;
                    elsif pend_wn = '1' then
                        pend_wn <= '0';
                        is_read <= '0';
                        last_op_was_stream <= '0';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if pend_wn_next = '1' then
                            buf_a <= buf_a_next;
                            buf_wd <= buf_wd_next;
                            pend_wn <= '1';
                            pend_wn_next <= '0';
                            pend_wn_same_row <= pend_wn_next_same_row;
                            pend_wn_next_same_row <= '0';
                        end if;
                        -- Page-mode: skip activate if same row
                        if row_open = '1' and pend_wn_same_row = '1' then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                    elsif pend_wn_next = '1' and row_open = '1'
                          and pend_wn_next_same_row = '0' then
                        -- Same cross-row precharge for the pipelined next write.
                        sdram_s_waitrequest <= '1';
                        state <= ST_PRE2;
                    elsif pend_wn_next = '1' then
                        bank_r <= buf_a_next(21 downto 20); row_r <= buf_a_next(19 downto 8); col_r <= buf_a_next(7 downto 0);
                        buf_a <= buf_a_next;
                        buf_wd <= buf_wd_next;
                        pend_wn <= '1';
                        pend_wn_next <= '0';
                        pend_wn_same_row <= pend_wn_next_same_row;
                        pend_wn_next_same_row <= '0';
                        is_read <= '0';
                        last_op_was_stream <= '0';
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if row_open = '1' and pend_wn_next_same_row = '1' then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                    elsif pend_rn = '1' then
                        is_read <= '1';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        if row_open = '1' then
                            -- Close row before read, then activate new row.
                            -- Keep pend_rn asserted across the precharge so the
                            -- read is not dropped: ST_PRE2 -> ST_TRP2 ->
                            -- ST_DEASSERT re-runs this arm with row_open=0.
                            state <= ST_PRE2;
                        else
                            pend_rn <= '0';
                            state <= ST_ACT;
                        end if;
                    elsif bp = '1' or (capture_burst_req = '1' and capture_burst_stop = '0') then
                        bp <= '1'; busy_r <= '1'; cnt <= 0;
                        sdram_s_waitrequest <= '1';
                        state <= ST_BPEND;
                    else
                        -- No pending work: OPEN-PAGE policy. Keep the row OPEN and
                        -- go idle, so the next (typically same-row) streaming write
                        -- enters ST_STREAM_WR directly without a re-ACTIVATE.
                        -- Closing here forced ACT+write+PRE for EVERY sample at
                        -- sparse capture rates (page-mode only survived back-to-back
                        -- writes) -- the deep-capture throughput ceiling. ST_IDLE
                        -- still precharges before refresh, and cross-row writes/reads
                        -- still precharge as needed, so correctness is unchanged.
                        state <= ST_IDLE;
                    end if;

            end case;

            write_depth <= v_depth;
            if v_peak > max_write_depth then
                max_write_depth <= v_peak;
            end if;
            s_cs_r   <= s_cs;
            s_ras_r  <= s_ras;
            s_cas_r  <= s_cas;
            s_we_r   <= s_we;
            s_ba_r   <= s_ba;
            s_addr_r <= s_addr;

        end if;
    end process;

end rtl;
