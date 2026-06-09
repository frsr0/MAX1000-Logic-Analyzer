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
        PWM_CARRIER_MAX : natural := 256;
        FADE_STEPS_MAX  : natural := 511;

        BREATH_IDLE_OFF  : natural := 5;
        BREATH_IDLE_RISE : natural := 255;
        BREATH_IDLE_ON   : natural := 100;
        BREATH_IDLE_FALL : natural := 255;

        CONFIRM_SPEED     : natural := 2;
        CONFIRM_OFF       : natural := 3;
        CONFIRM_RISE      : natural := 127;
        CONFIRM_ON        : natural := 50;
        CONFIRM_FALL      : natural := 127;
        CONFIRM_CYCLES    : natural := 3;

        ARMED_SPEED       : natural := 3;
        ARMED_OFF         : natural := 2;
        ARMED_RISE        : natural := 85;
        ARMED_ON          : natural := 33;
        ARMED_FALL        : natural := 85;

        FLASH_TICKS_ON    : natural := 18;
        FLASH_TICKS_OFF   : natural := 18;
        FLASH_STEP        : natural := 16;

        ROLL_TICK_DIV     : natural := 2;
        ROLL_PHASE_STEP   : natural := 85;
        ROLL_SPEED_MIN    : natural := 1;
        ROLL_SPEED_MAX    : natural := 8;
        ROLL_ACT_AVG      : natural := 8
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

    type led_state_t is (
        ST_IDLE,
        ST_HOST_CONFIRM,
        ST_TRIGGER_ARMED,
        ST_SINGLE_CAPTURE,
        ST_ROLLING_CAPTURE
    );

    type breath_state_t is (BR_OFF, BR_RISE, BR_ON, BR_FALL);

    function triangle(phase : natural range 0 to 255) return integer is
    begin
        if phase < 128 then
            return phase * 2;
        else
            return (255 - phase) * 2;
        end if;
    end function;

    -- Input pipeline registers to break long combinatorial path from
    -- capture engine (Fast_Logic_Analyzer_SDRAM) through hierarchy boundaries.
    signal armed_r       : std_logic := '0';
    signal capture_run_r : std_logic := '0';
    signal capture_full_r: std_logic := '0';
    signal cont_mode_r   : std_logic := '0';
    signal host_conn_r   : std_logic := '0';
    signal fifo_act_r    : std_logic_vector(3 downto 0) := (others => '0');
    signal fade_tick_r   : std_logic := '0';

    -- Pipeline registers for rolling capture animation speed.
    -- r_speed depends on r_act_sum * (ROLL_SPEED_MAX - ROLL_SPEED_MIN) / (ROLL_ACT_AVG * 4),
    -- which creates a long combinatorial path through multipliers and dividers.
    -- Registering r_speed breaks this into two 96 MHz cycles.
    signal r_speed_r     : natural range 1 to 8 := 1;

begin

    -- Input pipeline registers
    process(clk)
    begin
        if rising_edge(clk) then
            armed_r       <= armed;
            capture_run_r <= capture_run;
            capture_full_r<= capture_full;
            cont_mode_r   <= continuous_mode;
            host_conn_r   <= host_connected;
            fifo_act_r    <= fifo_activity;
            fade_tick_r   <= fade_tick;
        end if;
    end process;

    process(clk)
        variable state     : led_state_t := ST_IDLE;
        variable next_s    : led_state_t := ST_IDLE;

        variable hc_d1     : std_logic := '0';

        variable b_state   : breath_state_t := BR_OFF;
        variable b_timer   : natural range 0 to 255 := 0;
        variable b_cycle   : natural range 0 to 7 := 0;
        variable b_delta   : natural range 1 to 8 := 1;
        variable b_off_t   : natural range 0 to 255 := 5;
        variable b_rise_t  : natural range 0 to 255 := 255;
        variable b_on_t    : natural range 0 to 255 := 100;
        variable b_fall_t  : natural range 0 to 255 := 255;

        variable fl_on     : boolean := true;
        variable fl_timer  : natural range 0 to 255 := 0;

        variable r_phase   : natural range 0 to 255 := 0;
        variable r_div     : natural range 0 to 15 := 0;
        variable r_act_sum : natural range 0 to 255 := 0;
        variable r_act_cnt : natural range 0 to 255 := 0;
        variable r_speed_var : natural range 1 to 8 := 1;

        variable p, q      : natural range 0 to 1023;
        variable activity  : natural range 0 to 4;
    begin
        if rising_edge(clk) then
            if rst = '1' then
                state     := ST_IDLE;
                b_state   := BR_OFF;
                b_timer   := 0;
                b_cycle   := 0;
                fl_on     := true;
                fl_timer  := 0;
                r_phase   := 0;
                r_div     := 0;
                r_speed_var := ROLL_SPEED_MIN;
                r_act_sum := 0;
                r_act_cnt := 0;
                hc_d1     := '0';
                led_target <= (others => 0);
                fade_step  <= (others => 1);
            else
                -- State transitions (hc_d1 is from previous cycle — updated at end)
                case state is
                    when ST_IDLE =>
                        if host_conn_r = '1' and hc_d1 = '0' then
                            next_s := ST_HOST_CONFIRM;
                        elsif armed_r = '1' and capture_run_r = '0' then
                            next_s := ST_TRIGGER_ARMED;
                        else
                            next_s := ST_IDLE;
                        end if;

                    when ST_HOST_CONFIRM =>
                        if b_cycle >= CONFIRM_CYCLES
                           and b_state = BR_OFF and b_timer = 0 then
                            if armed = '1' and capture_run = '0' then
                                next_s := ST_TRIGGER_ARMED;
                            else
                                next_s := ST_IDLE;
                            end if;
                        else
                            next_s := ST_HOST_CONFIRM;
                        end if;

                    when ST_TRIGGER_ARMED =>
                        if capture_run_r = '1' then
                            if cont_mode_r = '1' then
                                next_s := ST_ROLLING_CAPTURE;
                            else
                                next_s := ST_SINGLE_CAPTURE;
                            end if;
                        else
                            next_s := ST_TRIGGER_ARMED;
                        end if;

                    when ST_SINGLE_CAPTURE =>
                        if capture_full_r = '1' or capture_run_r = '0' then
                            if armed_r = '1' then
                                next_s := ST_TRIGGER_ARMED;
                            else
                                next_s := ST_IDLE;
                            end if;
                        else
                            next_s := ST_SINGLE_CAPTURE;
                        end if;

                    when ST_ROLLING_CAPTURE =>
                        if capture_full_r = '1' or capture_run_r = '0' then
                            next_s := ST_IDLE;
                        else
                            next_s := ST_ROLLING_CAPTURE;
                        end if;
                end case;

                if state /= next_s and next_s = ST_HOST_CONFIRM then
                    b_state := BR_OFF;
                    b_timer := 0;
                    b_cycle := 0;
                end if;

                if fade_tick_r = '1' then
                    case state is
                        when ST_IDLE =>
                            b_delta  := 1;
                            b_off_t  := BREATH_IDLE_OFF;
                            b_rise_t := BREATH_IDLE_RISE;
                            b_on_t   := BREATH_IDLE_ON;
                            b_fall_t := BREATH_IDLE_FALL;

                            case b_state is
                                when BR_OFF =>
                                    if b_timer >= b_off_t then
                                        b_state := BR_RISE; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_RISE =>
                                    if b_timer >= b_rise_t then
                                        b_state := BR_ON; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_ON =>
                                    if b_timer >= b_on_t then
                                        b_state := BR_FALL; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_FALL =>
                                    if b_timer >= b_fall_t then
                                        b_state := BR_OFF; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                            end case;

                            if b_state = BR_RISE or b_state = BR_ON then
                                led_target(0) <= 255;
                            else
                                led_target(0) <= 0;
                            end if;
                            for i in 1 to 7 loop
                                led_target(i) <= 0;
                            end loop;
                            fade_step <= (others => b_delta);

                        when ST_HOST_CONFIRM =>
                            b_delta  := CONFIRM_SPEED;
                            b_off_t  := CONFIRM_OFF;
                            b_rise_t := CONFIRM_RISE;
                            b_on_t   := CONFIRM_ON;
                            b_fall_t := CONFIRM_FALL;

                            case b_state is
                                when BR_OFF =>
                                    if b_timer >= b_off_t then
                                        b_state := BR_RISE; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_RISE =>
                                    if b_timer >= b_rise_t then
                                        b_state := BR_ON; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_ON =>
                                    if b_timer >= b_on_t then
                                        b_state := BR_FALL; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_FALL =>
                                    if b_timer >= b_fall_t then
                                        b_state := BR_OFF; b_timer := 0;
                                        b_cycle := b_cycle + 1;
                                    else b_timer := b_timer + 1; end if;
                            end case;

                            for i in 0 to 7 loop
                                if b_state = BR_RISE or b_state = BR_ON then
                                    led_target(i) <= 255;
                                else
                                    led_target(i) <= 0;
                                end if;
                                fade_step(i) <= b_delta;
                            end loop;

                        when ST_TRIGGER_ARMED =>
                            b_delta  := ARMED_SPEED;
                            b_off_t  := ARMED_OFF;
                            b_rise_t := ARMED_RISE;
                            b_on_t   := ARMED_ON;
                            b_fall_t := ARMED_FALL;

                            case b_state is
                                when BR_OFF =>
                                    if b_timer >= b_off_t then
                                        b_state := BR_RISE; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_RISE =>
                                    if b_timer >= b_rise_t then
                                        b_state := BR_ON; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_ON =>
                                    if b_timer >= b_on_t then
                                        b_state := BR_FALL; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                                when BR_FALL =>
                                    if b_timer >= b_fall_t then
                                        b_state := BR_OFF; b_timer := 0;
                                    else b_timer := b_timer + 1; end if;
                            end case;

                            for i in 0 to 7 loop
                                if b_state = BR_RISE or b_state = BR_ON then
                                    led_target(i) <= 255;
                                else
                                    led_target(i) <= 0;
                                end if;
                                fade_step(i) <= b_delta;
                            end loop;

                        when ST_SINGLE_CAPTURE =>
                            if fl_on then
                                if fl_timer >= FLASH_TICKS_ON then
                                    fl_on := false; fl_timer := 0;
                                else fl_timer := fl_timer + 1; end if;
                            else
                                if fl_timer >= FLASH_TICKS_OFF then
                                    fl_on := true; fl_timer := 0;
                                else fl_timer := fl_timer + 1; end if;
                            end if;

                            for i in 0 to 7 loop
                                if fl_on then
                                    led_target(i) <= 255;
                                else
                                    led_target(i) <= 0;
                                end if;
                                fade_step(i) <= FLASH_STEP;
                            end loop;

                        when ST_ROLLING_CAPTURE =>
                            activity := 0;
                            if fifo_act_r(0) = '1' then activity := activity + 1; end if;
                            if fifo_act_r(1) = '1' then activity := activity + 1; end if;
                            if fifo_act_r(2) = '1' then activity := activity + 1; end if;
                            if fifo_act_r(3) = '1' then activity := activity + 1; end if;

                            r_act_sum := r_act_sum + activity;
                            r_act_cnt := r_act_cnt + 1;
                            if r_act_cnt >= ROLL_ACT_AVG then
                                r_act_cnt := 0;
                                r_speed_var := ROLL_SPEED_MIN
                                    + (r_act_sum * (ROLL_SPEED_MAX - ROLL_SPEED_MIN))
                                    / (ROLL_ACT_AVG * 4);
                                if r_speed_var < ROLL_SPEED_MIN then
                                    r_speed_var := ROLL_SPEED_MIN;
                                end if;
                                if r_speed_var > ROLL_SPEED_MAX then
                                    r_speed_var := ROLL_SPEED_MAX;
                                end if;
                                r_act_sum := 0;
                            end if;
                            r_speed_r <= r_speed_var;

                            if r_div >= r_speed_r - 1 then
                                r_div := 0;
                                if r_phase = 255 then
                                    r_phase := 0;
                                else
                                    r_phase := r_phase + 1;
                                end if;
                            else
                                r_div := r_div + 1;
                            end if;

                            for i in 0 to 7 loop
                                if ch_4_mode = '1' and i > 3 then
                                    led_target(i) <= 0;
                                else
                                    p := r_phase + i * ROLL_PHASE_STEP;
                                    q := p mod 256;
                                    led_target(i) <= triangle(q);
                                end if;
                                fade_step(i) <= 1;
                            end loop;
                    end case;
                end if;

                hc_d1 := host_conn_r;
                state := next_s;
            end if;
        end if;
    end process;

end rtl;
