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
        BLINK_SLOW_TOP : natural := 12500000
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
                            roll_phase <= roll_phase + 1;
                        end if;
                    else
                        roll_tick <= roll_tick + 1;
                    end if;
                else
                    roll_phase <= 0;
                    roll_tick <= 0;
                end if;

                -- LED target assignment
                if host_connected = '1' then
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

                for i in 4 to 7 loop
                    if capture_run = '1' and roll_phase = i - 4 then
                        led_target(i) <= 255;
                    else
                        led_target(i) <= 0;
                    end if;
                end loop;

                fade_step <= (others => 16);
            end if;
        end if;
    end process;

end rtl;
