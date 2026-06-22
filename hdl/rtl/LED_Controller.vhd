library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

package led_controller_pkg is
    type led_bright_array is array(0 to 7) of integer range 0 to 255;
    type led_step_array  is array(0 to 7) of integer range 1 to 32;
end package;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.led_controller_pkg.all;

entity LED_Controller is
    generic (
        BLINK_FAST_TOP : natural := 2500000;
        BLINK_SLOW_TOP : natural := 12500000;
        -- Host-connect "confirm" animation: on a 0->1 edge of host_connected the
        -- controller plays a brief pulse on LED0 (off -> rise -> on -> fall),
        -- repeated CONFIRM_CYCLES times, driving fade_step = 2 throughout so the
        -- top-level fade engine ramps smoothly. Durations are in fade ticks.
        CONFIRM_CYCLES : natural := 1;
        CONFIRM_OFF    : natural := 1;
        CONFIRM_RISE   : natural := 3;
        CONFIRM_ON     : natural := 1;
        CONFIRM_FALL   : natural := 3
    );
    port (
        clk             : in  std_logic;
        rst             : in  std_logic := '0';

        armed           : in  std_logic;
        capture_run     : in  std_logic;
        capture_full    : in  std_logic;
        continuous_mode : in  std_logic;
        host_connected  : in  std_logic;
        ch_4_mode       : in  std_logic := '0';

        fifo_activity   : in  std_logic_vector(3 downto 0) := (others => '0');

        fade_tick       : in  std_logic;

        led_target      : out led_bright_array := (others => 0);
        fade_step       : out led_step_array   := (others => 1)
    );
end LED_Controller;

architecture rtl of LED_Controller is
    signal blink_fast_cnt : natural range 0 to BLINK_FAST_TOP := 0;
    signal blink_slow_cnt : natural range 0 to BLINK_SLOW_TOP := 0;
    signal blink_fast : std_logic := '0';
    signal blink_slow : std_logic := '0';
    signal roll_phase : natural range 0 to 3 := 0;
    signal roll_tick  : natural range 0 to 5000000 := 0;

    -- Host-connect confirm animation (advances on fade_tick)
    type confirm_phase_t is (CP_IDLE, CP_OFF, CP_RISE, CP_ON, CP_FALL);
    signal confirm_phase       : confirm_phase_t := CP_IDLE;
    signal confirm_cnt         : natural range 0 to 255 := 0;
    signal confirm_cycles_left : natural range 0 to 255 := 0;
    signal confirm_active      : std_logic := '0';
    signal host_prev           : std_logic := '0';
begin

    process(clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                blink_fast_cnt <= 0;
                blink_slow_cnt <= 0;
                blink_fast <= '0';
                blink_slow <= '0';
                roll_phase <= 0;
                roll_tick <= 0;
                confirm_phase <= CP_IDLE;
                confirm_cnt <= 0;
                confirm_cycles_left <= 0;
                confirm_active <= '0';
                host_prev <= '0';
                led_target <= (others => 0);
                fade_step <= (others => 1);
            else
                -- Blink counters (independent fast/slow)
                if blink_fast_cnt >= BLINK_FAST_TOP - 1 then
                    blink_fast_cnt <= 0;
                    blink_fast <= not blink_fast;
                else
                    blink_fast_cnt <= blink_fast_cnt + 1;
                end if;
                if blink_slow_cnt >= BLINK_SLOW_TOP - 1 then
                    blink_slow_cnt <= 0;
                    blink_slow <= not blink_slow;
                else
                    blink_slow_cnt <= blink_slow_cnt + 1;
                end if;

                -- Rolling activity phase
                if capture_run = '1' and continuous_mode = '1' then
                    if roll_tick >= 5000000 - 1 then
                        roll_tick <= 0;
                        if fifo_activity /= "0000" then
                            if roll_phase = 3 then
                                roll_phase <= 0;
                            else
                                roll_phase <= roll_phase + 1;
                            end if;
                        end if;
                    else
                        roll_tick <= roll_tick + 1;
                    end if;
                else
                    roll_phase <= 0;
                    roll_tick <= 0;
                end if;

                -- Host-connect edge -> start confirm animation
                host_prev <= host_connected;
                if host_connected = '1' and host_prev = '0' and confirm_active = '0' then
                    confirm_active      <= '1';
                    confirm_phase       <= CP_OFF;
                    confirm_cnt         <= CONFIRM_OFF;
                    confirm_cycles_left <= CONFIRM_CYCLES;
                end if;

                -- Advance the confirm animation one step per fade tick
                if confirm_active = '1' and fade_tick = '1' then
                    if confirm_cnt > 1 then
                        confirm_cnt <= confirm_cnt - 1;
                    else
                        case confirm_phase is
                            when CP_OFF  => confirm_phase <= CP_RISE; confirm_cnt <= CONFIRM_RISE;
                            when CP_RISE => confirm_phase <= CP_ON;   confirm_cnt <= CONFIRM_ON;
                            when CP_ON   => confirm_phase <= CP_FALL; confirm_cnt <= CONFIRM_FALL;
                            when others  =>  -- CP_FALL / CP_IDLE: end of one cycle
                                if confirm_cycles_left > 1 then
                                    confirm_cycles_left <= confirm_cycles_left - 1;
                                    confirm_phase <= CP_OFF;
                                    confirm_cnt   <= CONFIRM_OFF;
                                else
                                    confirm_active <= '0';
                                    confirm_phase  <= CP_IDLE;
                                end if;
                        end case;
                    end if;
                end if;

                -- ── fade_step: slew rate selected by overall state ──
                -- idle / rolling = 1 (slow fade), confirm = 2, armed = 3,
                -- capture = 16 (effectively instant). Confirm sits above armed so
                -- the connect pulse plays even while armed.
                if capture_run = '1' and continuous_mode = '1' then
                    fade_step <= (others => 1);
                elsif capture_run = '1' then
                    fade_step <= (others => 16);
                elsif confirm_active = '1' then
                    fade_step <= (others => 2);
                elsif armed = '1' then
                    fade_step <= (others => 3);
                else
                    fade_step <= (others => 1);
                end if;

                -- ── LED brightness targets ──
                -- LED0: host-connect indicator; during the confirm animation it
                -- pulses (lit in the ON/FALL phases) for a visible acknowledgement.
                if confirm_active = '1' then
                    if confirm_phase = CP_ON or confirm_phase = CP_FALL then
                        led_target(0) <= 255;
                    else
                        led_target(0) <= 0;
                    end if;
                elsif host_connected = '1' then
                    led_target(0) <= 255;
                else
                    led_target(0) <= 0;
                end if;

                if armed = '1' and capture_run = '0' then
                    if blink_slow = '1' then
                        led_target(1) <= 0;
                    else
                        led_target(1) <= 255;
                    end if;
                else
                    led_target(1) <= 0;
                end if;

                if capture_run = '1' then
                    led_target(2) <= 255;
                else
                    led_target(2) <= 0;
                end if;

                if capture_full = '1' then
                    if blink_slow = '0' then
                        led_target(3) <= 255;
                    else
                        led_target(3) <= 0;
                    end if;
                else
                    led_target(3) <= 0;
                end if;

                -- LEDs 4-7: rolling activity indicator. Suppressed in 4-channel
                -- mode (those channels aren't captured, so their LEDs stay dark).
                for i in 4 to 7 loop
                    if ch_4_mode = '1' then
                        led_target(i) <= 0;
                    elsif capture_run = '1' and roll_phase = i - 4 then
                        led_target(i) <= 255;
                    else
                        led_target(i) <= 0;
                    end if;
                end loop;
            end if;
        end if;
    end process;

end rtl;
