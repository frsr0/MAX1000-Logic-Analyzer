library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Minimal package: keep same types so OLS_SDRAM_Top instantiation compiles.
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
        BLINK_TOP : natural := 12500000;  -- blink counter top (~8 Hz at 100 MHz)
        -- Sweep step top: deliberate scanner sweep during capture, not a blur.
        -- 7,000,000 @ 100 MHz sys_clk = 70 ms/step (~14.3 Hz steps); the bar
        -- bounces 0->6->0 (12 steps/cycle) so a full sweep takes ~0.84 s.
        SWEEP_TOP : natural := 7000000
    );
    port (
        clk             : in  std_logic;
        rst             : in  std_logic := '0';

        armed           : in  std_logic;
        capture_run     : in  std_logic;
        capture_full    : in  std_logic;
        host_connected  : in  std_logic;

        fade_tick       : in  std_logic;

        led_target      : out led_bright_array := (others => 0);
        fade_step       : out led_step_array   := (others => 1)
    );
end LED_Controller;

architecture rtl of LED_Controller is
    signal blink_cnt   : natural range 0 to BLINK_TOP := 0;
    signal blink       : std_logic := '0';

    -- Sweep bar: position 0..6, lit span = sweep_pos .. sweep_pos+1
    signal sweep_pos  : natural range 0 to 6 := 0;
    signal sweep_dir  : std_logic := '1';  -- '1' = right, '0' = left
    signal sweep_tick : natural range 0 to SWEEP_TOP := 0;

    type state_t is (ST_IDLE, ST_ARMED, ST_CAPTURE, ST_DONE);
    signal state     : state_t := ST_IDLE;
    signal done_cnt  : natural range 0 to 2 := 0;
    signal prev_full : std_logic := '0';
begin

    process(clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                blink_cnt <= 0;
                blink <= '0';
                sweep_pos <= 0;
                sweep_dir <= '1';
                sweep_tick <= 0;
                state <= ST_IDLE;
                done_cnt <= 0;
                prev_full <= '0';
                led_target <= (others => 0);
                fade_step <= (others => 1);
            else
                -- Single blink counter for armed/done blinking
                if blink_cnt >= BLINK_TOP - 1 then
                    blink_cnt <= 0;
                    blink <= not blink;
                else
                    blink_cnt <= blink_cnt + 1;
                end if;

                -- Sweep tick: advances the bar position during CAPTURE
                if sweep_tick >= SWEEP_TOP - 1 then
                    sweep_tick <= 0;
                    if state = ST_CAPTURE then
                        if sweep_dir = '1' then
                            if sweep_pos = 6 then
                                sweep_dir <= '0';
                                sweep_pos <= 5;
                            else
                                sweep_pos <= sweep_pos + 1;
                            end if;
                        else
                            if sweep_pos = 0 then
                                sweep_dir <= '1';
                                sweep_pos <= 1;
                            else
                                sweep_pos <= sweep_pos - 1;
                            end if;
                        end if;
                    end if;
                else
                    sweep_tick <= sweep_tick + 1;
                end if;

                prev_full <= capture_full;

                -- State machine: idle -> armed -> capture -> done -> idle
                case state is
                    when ST_IDLE =>
                        if armed = '1' then
                            state <= ST_ARMED;
                        end if;

                    when ST_ARMED =>
                        if capture_run = '1' then
                            state <= ST_CAPTURE;
                        elsif armed = '0' then
                            state <= ST_IDLE;
                        end if;

                    when ST_CAPTURE =>
                        if capture_full = '1' and prev_full = '0' then
                            state <= ST_DONE;
                            done_cnt <= 2;
                        end if;

                    when ST_DONE =>
                        if capture_run = '1' then
                            state <= ST_CAPTURE;  -- back-to-back re-arm
                        elsif blink = '1' then
                            if done_cnt > 0 then
                                done_cnt <= done_cnt - 1;
                            else
                                state <= ST_IDLE;
                            end if;
                        end if;
                end case;

                -- fade_step by state
                if state = ST_CAPTURE then
                    fade_step <= (others => 16);  -- instant on/off for sweep bar
                else
                    fade_step <= (others => 1);   -- slow fade for indicator LEDs
                end if;

                -- LED targets
                if state = ST_CAPTURE then
                    -- Sweep bar: light the LED at sweep_pos and sweep_pos+1
                    for i in 0 to 7 loop
                        if i = sweep_pos or i = sweep_pos + 1 then
                            led_target(i) <= 255;
                        else
                            led_target(i) <= 0;
                        end if;
                    end loop;
                else
                    -- Normal indicator mode
                    if host_connected = '1' then
                        led_target(0) <= 255;
                    else
                        led_target(0) <= 0;
                    end if;

                    if state = ST_ARMED then
                        if blink = '0' then
                            led_target(1) <= 255;
                        else
                            led_target(1) <= 0;
                        end if;
                    else
                        led_target(1) <= 0;
                    end if;

                    led_target(2) <= 0;  -- used by sweep above, off in indicator mode

                    if state = ST_DONE then
                        if blink = '0' then
                            led_target(3) <= 255;
                        else
                            led_target(3) <= 0;
                        end if;
                    else
                        led_target(3) <= 0;
                    end if;

                    led_target(4) <= 0;
                    led_target(5) <= 0;
                    led_target(6) <= 0;
                    led_target(7) <= 0;
                end if;

            end if;
        end if;
    end process;

end rtl;
