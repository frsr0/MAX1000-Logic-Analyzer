library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SDRAM_Controller is
  generic (
    CLK_Frequency : natural := 12000000  -- Hz
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
    sdram_s_burst         : in std_logic := 'X';
    sdram_s_readdata      : out std_logic_vector(15 downto 0);
    sdram_s_readdatavalid : out std_logic;
    sdram_s_waitrequest   : out std_logic;
    sdram_s_idle          : out std_logic;

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

    -- SDRAM timing parameters (nanoseconds)
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
        ST_PRE2, ST_TRP2,
        ST_RFSH_PRE, ST_RFSH, ST_TRFC,
        ST_DEASSERT
    );
    signal state : state_type := ST_INIT;

    signal cnt   : integer range 0 to 65535 := 0;
    signal timer : integer range 0 to 65535 := 0;
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

    -- Burst FIFO: stores up to 4 addr+data pairs during burst load
    type burst_fifo_array is array(0 to 3) of std_logic_vector(37 downto 0);
    signal burst_fifo      : burst_fifo_array := (others => (others => '0'));
    signal burst_fifo_cnt  : natural range 0 to 4 := 0;
    signal burst_fifo_head : natural range 0 to 3 := 0;
    signal burst_fifo_tail : natural range 0 to 3 := 0;
    signal burst_active    : std_logic := '0';
    signal burst_cnt       : natural range 0 to 3 := 0;

    signal dq_oe : std_logic := '0';

    signal bank_r : std_logic_vector(1 downto 0) := "00";
    signal row_r  : std_logic_vector(11 downto 0) := (others => '0');
    signal col_r  : std_logic_vector(7 downto 0) := (others => '0');

    signal s_cs  : std_logic := '1';
    signal s_ras : std_logic := '1';
    signal s_cas : std_logic := '1';
    signal s_we  : std_logic := '1';
    signal s_addr : std_logic_vector(11 downto 0) := (others => '0');
    signal s_ba   : std_logic_vector(1 downto 0) := (others => '0');

    signal write_depth : natural := 0;
    signal max_write_depth : natural := 0;
    signal prev_buf_a : std_logic_vector(21 downto 0) := (others => '0');

    -- Page-mode: track open row
    signal active_row  : std_logic_vector(11 downto 0) := (others => '0');
    signal active_bank : std_logic_vector(1 downto 0) := (others => '0');
    signal row_open    : std_logic := '0';

    constant MR : std_logic_vector(11 downto 0) := "000001000000";

    function is_same_row(addr : std_logic_vector(21 downto 0);
                         row  : std_logic_vector(11 downto 0);
                         bank : std_logic_vector(1 downto 0)) return boolean is
    begin
        return addr(19 downto 8) = row and addr(21 downto 20) = bank;
    end function;

begin

    sdram_cke <= '1';
    sdram_dqm <= "00";

    sdram_cs_n <= s_cs;
    sdram_ras_n <= s_ras;
    sdram_cas_n <= s_cas;
    sdram_we_n  <= s_we;
    sdram_addr  <= s_addr;
    sdram_ba    <= s_ba;

    sdram_dq <= buf_wd when dq_oe = '1' else (others => 'Z');
    sdram_s_idle <= '1' when state = ST_IDLE else '0';

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
            cnt <= 0; timer <= 0; ref_req <= '0';
            dq_oe <= '0';
            sdram_s_waitrequest <= '1'; sdram_s_readdatavalid <= '0';
            sdram_s_readdata <= (others => '0');
            last_rn <= '1'; last_wn <= '1';
            pend_rn <= '0'; pend_wn <= '0'; pend_wn_next <= '0';
            buf_a <= (others => '0'); buf_wd <= (others => '0');
            buf_a_next <= (others => '0'); buf_wd_next <= (others => '0');
            is_read <= '0';
            s_cs <= '1'; s_ras <= '1'; s_cas <= '1'; s_we <= '1';
            s_addr <= (others => '0'); s_ba <= (others => '0');
            write_depth <= 0; max_write_depth <= 0;
            prev_buf_a <= (others => '0');
            row_open <= '0'; active_row <= (others => '0'); active_bank <= (others => '0');
            burst_fifo_cnt <= 0; burst_active <= '0'; burst_cnt <= 0;

        elsif rising_edge(clk_in_clk) then

            last_rn <= sdram_s_read_n;
            last_wn <= sdram_s_write_n;

            if buf_a /= prev_buf_a then
                report "BUF_A: 0x" & to_hex(buf_a) & " (prev was 0x" & to_hex(prev_buf_a) & ")" severity note;
            end if;
            prev_buf_a <= buf_a;

            -- Refresh timer
            if timer >= REF_CYCLES - 1 then
                ref_req <= '1';
                timer <= 0;
            else
                timer <= timer + 1;
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
                if sdram_s_burst = '1' then
                    -- Burst load: push addr+data into internal FIFO
                    if burst_fifo_cnt < 4 then
                        burst_fifo(burst_fifo_head) <= sdram_s_address & sdram_s_writedata;
                        if burst_fifo_head = 3 then burst_fifo_head <= 0;
                        else burst_fifo_head <= burst_fifo_head + 1; end if;
                        burst_fifo_cnt <= burst_fifo_cnt + 1;
                    end if;
                else
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
                end if;
                pend_rn <= '0';
            end if;

            v_peak := v_depth;

            s_cs <= '0'; s_ras <= '1'; s_cas <= '1'; s_we <= '1';

            case state is

                -- INITIALIZATION
                when ST_INIT =>
                    s_cs <= '1';
                    if cnt < 19999 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_INIT_NOP;
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
                        pend_rn <= '0';
                        is_read <= '1';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        state <= ST_ACT;
                    elsif pend_wn = '1' then
                        pend_wn <= '0';
                        is_read <= '0';
                        dq_oe <= '1';
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
                        if row_open = '1' and is_same_row(buf_a, active_row, active_bank) then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                    elsif pend_wn_next = '1' then
                        bank_r <= buf_a_next(21 downto 20); row_r <= buf_a_next(19 downto 8); col_r <= buf_a_next(7 downto 0);
                        buf_a <= buf_a_next;
                        buf_wd <= buf_wd_next;
                        pend_wn <= '1';
                        pend_wn_next <= '0';
                        is_read <= '0';
                        dq_oe <= '1';
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if row_open = '1' and is_same_row(buf_a_next, active_row, active_bank) then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                    elsif burst_fifo_cnt = 4 then
                        -- Start burst write: pop first entry from FIFO
                        burst_active <= '1';
                        dq_oe <= '1';
                        buf_a <= burst_fifo(burst_fifo_tail)(37 downto 16);
                        buf_wd <= burst_fifo(burst_fifo_tail)(15 downto 0);
                        bank_r <= burst_fifo(burst_fifo_tail)(37 downto 36);
                        row_r  <= burst_fifo(burst_fifo_tail)(35 downto 24);
                        col_r  <= burst_fifo(burst_fifo_tail)(23 downto 16);
                        if burst_fifo_tail = 3 then burst_fifo_tail <= 0;
                        else burst_fifo_tail <= burst_fifo_tail + 1; end if;
                        burst_fifo_cnt <= burst_fifo_cnt - 1;
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if row_open = '1' and is_same_row(burst_fifo(burst_fifo_tail)(37 downto 16), active_row, active_bank) then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
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
                        if is_read = '1' then state <= ST_RD;
                        else state <= ST_WR;
                        end if;
                    end if;

                when ST_RD =>
                    s_cas <= '0';
                    s_addr <= "0000" & col_r;
                    s_ba <= bank_r;
                    state <= ST_CL_WAIT;

                when ST_CL_WAIT =>
                    if cnt < 1 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_RD_DATA;
                    end if;

                when ST_RD_DATA =>
                    sdram_s_readdata <= sdram_dq;
                    sdram_s_readdatavalid <= '1';
                    state <= ST_PRE2;

                when ST_WR =>
                    s_cas <= '0'; s_we <= '0';
                    s_addr <= "0000" & col_r;
                    s_ba <= bank_r;
                    if burst_active = '1' and burst_cnt < 3 then
                        -- Stay for next burst beat: drain FIFO entry
                        burst_cnt <= burst_cnt + 1;
                        if burst_fifo_cnt > 0 then
                            buf_a <= burst_fifo(burst_fifo_tail)(37 downto 16);
                            buf_wd <= burst_fifo(burst_fifo_tail)(15 downto 0);
                            col_r <= burst_fifo(burst_fifo_tail)(23 downto 16);
                            if burst_fifo_tail = 3 then burst_fifo_tail <= 0;
                            else burst_fifo_tail <= burst_fifo_tail + 1; end if;
                            burst_fifo_cnt <= burst_fifo_cnt - 1;
                        end if;
                        state <= ST_WR;
                    else
                        burst_active <= '0';
                        burst_cnt <= 0;
                        state <= ST_TWR;
                    end if;

                when ST_TWR =>
                    dq_oe <= '0';
                    burst_active <= '0';
                    burst_cnt <= 0;
                    -- For page-mode writes (same row pending), skip TWR delay.
                    -- tWR is only needed before precharge, not between same-row writes.
                    if (pend_wn = '1' and is_same_row(buf_a, active_row, active_bank))
                    or (pend_wn_next = '1' and is_same_row(buf_a_next, active_row, active_bank)) then
                        cnt <= 0; state <= ST_DEASSERT;
                    elsif cnt < TWR_CYCLES - 1 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_DEASSERT;
                    end if;

                when ST_PRE2 =>
                    sdram_s_readdatavalid <= '0';
                    s_ras <= '0'; s_we <= '0';
                    s_addr(10) <= '1'; s_ba <= bank_r;
                    row_open <= '0';
                    state <= ST_TRP2;

                when ST_TRP2 =>
                    if cnt < TRP_CYCLES - 1 then cnt <= cnt + 1;
                    else cnt <= 0; state <= ST_DEASSERT;
                    end if;

                -- REFRESH
                when ST_RFSH_PRE =>
                    if timer = 0 then
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
                    sdram_s_waitrequest <= '0';
                    if pend_wn = '1' then
                        pend_wn <= '0';
                        is_read <= '0';
                        dq_oe <= '1';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if pend_wn_next = '1' then
                            buf_a <= buf_a_next;
                            buf_wd <= buf_wd_next;
                            pend_wn <= '1';
                            pend_wn_next <= '0';
                        end if;
                        -- Page-mode: skip activate if same row
                        if row_open = '1' and is_same_row(buf_a, active_row, active_bank) then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                    elsif pend_wn_next = '1' then
                        bank_r <= buf_a_next(21 downto 20); row_r <= buf_a_next(19 downto 8); col_r <= buf_a_next(7 downto 0);
                        buf_a <= buf_a_next;
                        buf_wd <= buf_wd_next;
                        pend_wn <= '1';
                        pend_wn_next <= '0';
                        is_read <= '0';
                        dq_oe <= '1';
                        sdram_s_waitrequest <= '1';
                        v_depth := v_depth - 1;
                        if row_open = '1' and is_same_row(buf_a_next, active_row, active_bank) then
                            state <= ST_WR;
                        else
                            state <= ST_ACT;
                        end if;
                elsif burst_fifo_cnt = 4 then
                    -- Next burst waiting: start immediately
                    burst_active <= '1';
                    dq_oe <= '1';
                    buf_a <= burst_fifo(burst_fifo_tail)(37 downto 16);
                    buf_wd <= burst_fifo(burst_fifo_tail)(15 downto 0);
                    bank_r <= burst_fifo(burst_fifo_tail)(37 downto 36);
                    row_r  <= burst_fifo(burst_fifo_tail)(35 downto 24);
                    col_r  <= burst_fifo(burst_fifo_tail)(23 downto 16);
                    if burst_fifo_tail = 3 then burst_fifo_tail <= 0;
                    else burst_fifo_tail <= burst_fifo_tail + 1; end if;
                    burst_fifo_cnt <= burst_fifo_cnt - 1;
                    sdram_s_waitrequest <= '1';
                    v_depth := v_depth - 1;
                    if row_open = '1' and is_same_row(burst_fifo(burst_fifo_tail)(37 downto 16), active_row, active_bank) then
                        state <= ST_WR;
                    else
                        state <= ST_ACT;
                    end if;
                elsif pend_rn = '1' then
                        pend_rn <= '0';
                        is_read <= '1';
                        bank_r <= buf_a(21 downto 20); row_r <= buf_a(19 downto 8); col_r <= buf_a(7 downto 0);
                        sdram_s_waitrequest <= '1';
                        if row_open = '1' then
                            -- Close row before read, then activate new row
                            state <= ST_PRE2;
                        else
                            state <= ST_ACT;
                        end if;
                    else
                        -- No pending: go idle, close row if open
                        if row_open = '1' then
                            state <= ST_PRE2;
                        else
                            -- Check if refresh was requested while we were busy
                            if ref_req = '1' then
                                ref_req <= '0';
                                sdram_s_waitrequest <= '1';
                                state <= ST_RFSH;
                            else
                                state <= ST_IDLE;
                            end if;
                        end if;
                    end if;

            end case;

            write_depth <= v_depth;
            if v_peak > max_write_depth then
                max_write_depth <= v_peak;
            end if;

        end if;
    end process;

end rtl;
