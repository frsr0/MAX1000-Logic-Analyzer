library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- digital_rle : 4-slice run-length encoder for the 16 digital channels
-- ---------------------------------------------------------------------------
-- Runs in the fast_clk domain. The 16 logic pins are partitioned into four
-- 4-bit slices; each slice is tracked independently with its own 9-bit dwell
-- counter. A packet is queued for a slice whenever any pin in that slice
-- toggles, or its counter saturates at 511 (a "still-holding" marker so long
-- idle runs stay representable in 9 bits).
--
--   Packet (bit15='1'): bits[14:13] = 2-bit slice ID (0..3)
--                       bits[12:9]  = 4-bit slice VALUE held during the run
--                       bits[8:0]   = 9-bit dwell count of that run
--   Host run length = dwell + 1 (the counter is 0 on the first cycle of a run).
--
-- Self-describing / value-carrying format
-- ---------------------------------------
-- Every packet reports a *completed* run as the classic RLE pair
-- (value, count): the value the slice held and how long it held it. This makes
-- reconstruction unconditional -- the host never has to know the target pins in
-- advance, never replays a state sequence, and cannot desync:
--   * a saturation marker carries the same value as the run it extends, so the
--     host simply concatenates consecutive same-value packets (no type flag,
--     no 4095-vs-real-change ambiguity), and
--   * a dropped transition (see `overflow`) loses exactly one segment; every
--     later packet still decodes correctly because each is self-contained.
-- The only latency is the in-progress (tail) run of each slice, which is
-- flushed by saturation within 511 cycles (~2.55 us @ 200.4 MHz).
--
-- Note the value reported on a *change* is the value that just ENDED (prev),
-- not the new one; the new value arrives with that slice's next packet (or its
-- next saturation marker within 511 cycles). After reset prev = 0, so a slice
-- whose pins are non-zero at capture start emits a 1-cycle phantom "value 0"
-- run before adopting the real level -- arm/reset immediately before capture.
--
-- Tracking (change detect + counting) is fully decoupled from output: every
-- slice is evaluated every cycle, so concurrent toggles are each captured (they
-- queue into per-slice `pend` bits, drained round-robin one per accepted
-- handshake) and counters keep running through downstream backpressure -- a
-- single emit FSM that parked in a FLUSH state would go blind to the inputs
-- exactly while the FIFO was momentarily full.
--
-- Synchronous reset, registered outputs, no combinational loops.
entity digital_rle is
  port (
    clk          : in  std_logic;  -- fast_clk
    rst          : in  std_logic;  -- synchronous, active high
    clk_en       : in  std_logic := '1';
    digital_in   : in  std_logic_vector(15 downto 0);

    -- Output packet stream (valid/ready) to the arbiter mux
    packet_out   : out std_logic_vector(15 downto 0) := (others => '0');
    packet_valid : out std_logic := '0';
    packet_ready : in  std_logic := '1';

    overflow     : out std_logic := '0'  -- one segment lost: digital rate > drain rate
  );
end digital_rle;

architecture rtl of digital_rle is
  constant SAT_MAX : unsigned(8 downto 0) := to_unsigned(511, 9);

  type slice_val_arr is array(0 to 3) of std_logic_vector(3 downto 0);
  type slice_cnt_arr is array(0 to 3) of unsigned(8 downto 0);

  signal prev    : slice_val_arr := (others => (others => '0'));  -- current run value per slice
  signal cnt     : slice_cnt_arr := (others => (others => '0'));  -- current run dwell
  signal cap_dur : slice_cnt_arr := (others => (others => '0'));  -- queued dwell per pending slice
  signal cap_val : slice_val_arr := (others => (others => '0'));  -- queued value per pending slice
  signal pend    : std_logic_vector(3 downto 0) := (others => '0');
  signal rr      : unsigned(1 downto 0) := (others => '0');       -- round-robin drain pointer
  -- 3-stage drain pipeline:
  --   Stage 1 (always): round-robin search -> sel_pre, found_pre
  --   Stage 2 (advance when emit free): cap_val(sel_pre) -> cap_val_r, pend clear
  --   Stage 3: emit from cap_val_r/cap_dur_r
  signal sel_pre   : integer range 0 to 3 := 0;
  signal found_pre : std_logic := '0';
  signal sel_r     : integer range 0 to 3 := 0;
  signal found_r   : std_logic := '0';
  signal cap_val_r : std_logic_vector(3 downto 0) := (others => '0');
  signal cap_dur_r : unsigned(8 downto 0) := (others => '0');
  signal packet_valid_r : std_logic := '0';

  -- Combinational per-slice change flags (fed to the tracking process).
  signal changed : std_logic_vector(3 downto 0);
  signal changed_r : std_logic_vector(3 downto 0) := (others => '0');
begin
  packet_valid <= packet_valid_r;

  gen_changed : for i in 0 to 3 generate
    changed(i) <= '1' when digital_in(i*4+3 downto i*4) /= prev(i) else '0';
  end generate;

  process(clk)
    variable pend_v : std_logic_vector(3 downto 0);
    variable capd_v : slice_cnt_arr;
    variable capv_v : slice_val_arr;
    variable sat    : std_logic;
    variable sel    : integer range 0 to 3;
    variable found  : boolean;
    variable j      : integer range 0 to 6;   -- rr(0..3) + k(0..3) before wrap
  begin
    if rising_edge(clk) then
      if rst = '1' then
        prev         <= (others => (others => '0'));
        cnt          <= (others => (others => '0'));
        cap_dur      <= (others => (others => '0'));
        cap_val      <= (others => (others => '0'));
        pend         <= (others => '0');
        rr           <= (others => '0');
        packet_valid_r <= '0';
        packet_out   <= (others => '0');
        overflow     <= '0';
        sel_r     <= 0;
        found_r   <= '0';
        sel_pre   <= 0;
        found_pre <= '0';
        cap_val_r <= (others => '0');
        cap_dur_r <= (others => '0');
        changed_r  <= (others => '0');
      elsif clk_en = '1' then
        changed_r  <= changed;                 -- register change flags for timing
        overflow <= '0';                       -- single-cycle strobe
        pend_v   := pend;
        capd_v   := cap_dur;
        capv_v   := cap_val;

        -- ---- Tracking: evaluate every slice, every cycle ----
        for i in 0 to 3 loop
          if cnt(i) = SAT_MAX then sat := '1'; else sat := '0'; end if;

          if changed_r(i) = '1' or sat = '1' then
            if pend_v(i) = '0' then
              -- Queue the completed run (value held, dwell) and restart.
              capd_v(i) := cnt(i);
              capv_v(i) := prev(i);            -- value that was held this run
              pend_v(i) := '1';
              cnt(i)    <= (others => '0');
              if changed_r(i) = '1' then
                prev(i) <= digital_in(i*4+3 downto i*4);  -- adopt new value
              end if;
            else
              -- Previous packet for this slice not yet drained.
              if changed_r(i) = '1' then
                -- A genuine transition is being dropped: flag and resync so we
                -- do not spin re-detecting the same change forever. Only this
                -- one segment is lost; later packets still decode.
                overflow <= '1';
                prev(i)  <= digital_in(i*4+3 downto i*4);
                cnt(i)   <= (others => '0');
              end if;
              -- Saturate-while-pending: hold at max (no data lost); re-queues
              -- once the pending packet drains.
            end if;
          else
            cnt(i) <= cnt(i) + 1;
          end if;
        end loop;
        -- ---- Stage 1: round-robin search (every cycle) ----
        -- Searches for the next pending slice starting at rr. Always registers
        -- the result; stage 2 gates on found_pre to avoid redundant work.
        found := false;
        sel   := 0;
        for k in 0 to 3 loop
          j := to_integer(rr) + k;
          if j > 3 then j := j - 4; end if;
          if not found and pend(j) = '1' then
            sel   := j;
            found := true;
          end if;
        end loop;
        sel_pre <= sel;
        if found then found_pre <= '1'; else found_pre <= '0'; end if;

        -- ---- Stage 2: capture run data; advance only when stage 3 is free ----
        -- Reads cap_val/cap_dur through the registered sel_pre (breaking the
        -- search mux chain from the array read), then clears pend_v so the
        -- tracking loop can queue the next run. The pend clear and cap read
        -- are in the same cycle, so the tracking loop (which already ran this
        -- cycle) cannot overwrite the captured data.
        if found_pre = '1' and (found_r = '0' or (packet_valid_r = '1' and packet_ready = '1')) then
          sel_r     <= sel_pre;
          cap_val_r <= cap_val(sel_pre);
          cap_dur_r <= cap_dur(sel_pre);
          found_r   <= '1';
          pend_v(sel_pre) := '0';
          rr        <= to_unsigned((sel_pre + 1) mod 4, 2);
        elsif found_r = '1' and packet_valid_r = '1' and packet_ready = '1' then
          found_r   <= '0';
        end if;

        -- ---- Stage 3: emit packet from registered pipeline ----
        if found_r = '1' and (packet_valid_r = '0' or packet_ready = '1') then
          packet_out   <= '1'
                        & std_logic_vector(to_unsigned(sel_r, 2))
                        & cap_val_r
                        & std_logic_vector(cap_dur_r);
          packet_valid_r <= '1';
        elsif packet_ready = '1' then
          packet_valid_r <= '0';
        end if;



        pend    <= pend_v;
        cap_dur <= capd_v;
        cap_val <= capv_v;
      end if;
    end if;
  end process;

end rtl;
