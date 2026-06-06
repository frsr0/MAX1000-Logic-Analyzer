library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.led_controller_pkg.all;

entity tb_led_controller is
end tb_led_controller;

architecture sim of tb_led_controller is

    constant CLK_PERIOD : time := 20.833 ns;
    constant PWM_MAX    : natural := 256;
    constant FADE_MAX   : natural := 511;

    signal clk          : std_logic := '0';
    signal rst          : std_logic := '0';
    signal armed        : std_logic := '0';
    signal capture_run  : std_logic := '0';
    signal capture_full : std_logic := '0';
    signal continuous   : std_logic := '0';
    signal host_conn    : std_logic := '0';
    signal ch_4_mode    : std_logic := '0';
    signal fifo_act     : std_logic_vector(3 downto 0) := (others => '0');

    signal fade_tick    : std_logic;

    signal led_target   : led_bright_array := (others => 0);
    signal fade_step    : led_step_array   := (others => 1);

    signal pwm_cnt      : integer range 0 to PWM_MAX := 0;
    signal fade_cnt     : integer range 0 to FADE_MAX := 0;
    signal led_bright   : led_bright_array := (others => 0);

    procedure wait_fade_cycles(n : natural) is
    begin
        for i in 1 to n * (FADE_MAX + 1) * (PWM_MAX + 1) loop
            wait until rising_edge(clk);
        end loop;
        wait for 0 fs;
    end procedure;

    function to_string(n : natural) return string is
    begin
        return integer'image(n);
    end function;

begin

    clk <= not clk after CLK_PERIOD / 2;

    fade_tick <= '1' when (pwm_cnt = PWM_MAX - 1 and fade_cnt = FADE_MAX) else '0';

    process(clk)
    begin
        if rising_edge(clk) then
            if pwm_cnt = PWM_MAX then pwm_cnt <= 0;
            else pwm_cnt <= pwm_cnt + 1; end if;
            if pwm_cnt = PWM_MAX - 1 then
                if fade_cnt < FADE_MAX then fade_cnt <= fade_cnt + 1;
                else fade_cnt <= 0; end if;
            end if;
        end if;
    end process;

    process(clk)
    begin
        if rising_edge(clk) then
            if fade_tick = '1' then
                for i in 0 to 7 loop
                    if led_bright(i) < led_target(i) then
                        if led_bright(i) + fade_step(i) >= led_target(i) then
                            led_bright(i) <= led_target(i);
                        else
                            led_bright(i) <= led_bright(i) + fade_step(i);
                        end if;
                    elsif led_bright(i) > led_target(i) then
                        if led_bright(i) <= fade_step(i) then
                            led_bright(i) <= led_target(i);
                        elsif led_bright(i) - fade_step(i) <= led_target(i) then
                            led_bright(i) <= led_target(i);
                        else
                            led_bright(i) <= led_bright(i) - fade_step(i);
                        end if;
                    end if;
                end loop;
            end if;
        end if;
    end process;

    DUT: entity work.LED_Controller
        generic map (
            CONFIRM_CYCLES => 1,
            CONFIRM_OFF    => 1,
            CONFIRM_RISE   => 3,
            CONFIRM_ON     => 1,
            CONFIRM_FALL   => 3
        )
        port map (
            clk => clk, rst => rst,
            armed => armed, capture_run => capture_run,
            capture_full => capture_full, continuous_mode => continuous,
            host_connected => host_conn, ch_4_mode => ch_4_mode,
            fifo_activity => fifo_act, fade_tick => fade_tick,
            led_target => led_target, fade_step => fade_step
        );

    process
        variable all_ok : boolean := true;

        procedure check(cond : boolean; msg : string) is
        begin
            if not cond then
                report "FAIL: " & msg severity warning;
                all_ok := false;
            end if;
        end procedure;

    begin
        report "=== Test 1: Reset enters IDLE ===";
        rst <= '1';
        wait until rising_edge(clk);
        rst <= '0';
        wait_fade_cycles(2);
        check(fade_step(0) = 1, "fade_step should be 1 in IDLE");
        check(led_target(0) = 0 or led_target(0) = 255,
              "LED0 target should be 0 or 255 in IDLE");

        report "=== Test 2: IDLE - LED1-7 remain off ===";
        wait_fade_cycles(3);
        for i in 1 to 7 loop
            check(led_target(i) = 0,
                  "LED" & to_string(i) & " should be 0 in IDLE");
        end loop;

        report "=== Test 3: Host connected triggers confirm animation ===";
        host_conn <= '1';
        wait_fade_cycles(1);
        host_conn <= '0';
        wait_fade_cycles(3);
        check(fade_step(0) = 2,
              "fade_step should be 2 during confirm, got " & to_string(fade_step(0)));
        -- Wait for confirm to complete (1 cycle = 8 ticks, 10 for margin)
        wait_fade_cycles(10);
        check(fade_step(0) = 1,
              "after confirm should return to fade_step=1, got " & to_string(fade_step(0)));

        report "=== Test 4: Trigger armed ===";
        armed <= '1';
        capture_run <= '0';
        wait_fade_cycles(3);
        check(fade_step(0) = 3,
              "fade_step should be 3 in ARMED, got " & to_string(fade_step(0)));

        report "=== Test 5: Single capture flash ===";
        capture_run <= '1';
        wait_fade_cycles(3);
        check(fade_step(0) = 16,
              "fade_step should be 16 in CAPTURE, got " & to_string(fade_step(0)));

        report "=== Test 6: Return to armed after capture complete ===";
        capture_full <= '1';
        wait_fade_cycles(3);
        capture_full <= '0';
        capture_run <= '0';
        wait_fade_cycles(3);
        check(fade_step(0) = 3,
              "should return to ARMED (step=3), got " & to_string(fade_step(0)));

        report "=== Test 7: Rolling capture ===";
        continuous <= '1';
        capture_run <= '1';
        wait_fade_cycles(3);
        check(fade_step(0) = 1,
              "fade_step should be 1 in rolling, got " & to_string(fade_step(0)));
        fifo_act <= "1111";
        wait_fade_cycles(45);
        fifo_act <= "0000";
        wait_fade_cycles(45);

        report "=== Test 8: 4-channel mode ===";
        ch_4_mode <= '1';
        wait_fade_cycles(3);
        for i in 4 to 7 loop
            check(led_target(i) = 0,
                  "LED" & to_string(i) & " should be 0 in 4-ch mode");
        end loop;

        report "=== Test 9: Return to IDLE ===";
        capture_run <= '0';
        capture_full <= '1';
        continuous <= '0';
        armed <= '0';
        wait_fade_cycles(3);
        check(fade_step(0) = 1,
              "fade_step should return to 1 in IDLE, got " & to_string(fade_step(0)));

        if all_ok then
            report "=== ALL TESTS PASSED ===";
        else
            report "=== SOME TESTS FAILED ===" severity warning;
        end if;
        wait;
    end process;

end sim;
