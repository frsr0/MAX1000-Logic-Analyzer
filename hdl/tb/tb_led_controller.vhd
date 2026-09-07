library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.led_controller_pkg.all;

entity tb_led_controller is
end tb_led_controller;

architecture sim of tb_led_controller is

    constant CLK_PERIOD : time := 20.833 ns;

    signal clk          : std_logic := '0';
    signal rst          : std_logic := '0';
    signal armed        : std_logic := '0';
    signal capture_run  : std_logic := '0';
    signal capture_full : std_logic := '0';
    signal host_conn    : std_logic := '0';

    signal fade_tick    : std_logic := '0';

    signal led_target   : led_bright_array := (others => 0);
    signal fade_step    : led_step_array   := (others => 1);

    procedure wait_cycles(n : natural) is
    begin
        for i in 1 to n loop
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

    -- Current LED_Controller entity (BLINK_TOP/SWEEP_TOP generics; ports
    -- clk/rst/armed/capture_run/capture_full/host_connected/fade_tick/
    -- led_target/fade_step). The old CONFIRM_*/fifo_activity/ch_4_mode/
    -- continuous_mode interface was removed in the LED redesign.
    DUT: entity work.LED_Controller
        generic map (
            BLINK_TOP => 4,
            SWEEP_TOP => 3
        )
        port map (
            clk => clk, rst => rst,
            armed => armed, capture_run => capture_run,
            capture_full => capture_full, host_connected => host_conn,
            fade_tick => fade_tick,
            led_target => led_target, fade_step => fade_step
        );

    process
        variable all_ok : boolean := true;

        procedure check(cond : boolean; msg : string) is
        begin
            if not cond then
                report "FAIL: " & msg severity failure;
                all_ok := false;
            end if;
        end procedure;

    begin
        report "=== Test 1: Reset enters IDLE ===";
        rst <= '1';
        wait until rising_edge(clk);
        rst <= '0';
        wait_cycles(2);
        check(fade_step(0) = 1, "fade_step should be 1 in IDLE, got " & to_string(fade_step(0)));
        check(led_target(0) = 0, "LED0 target should be 0 in IDLE (no host)");

        report "=== Test 2: Host connected lights LED0 ===";
        host_conn <= '1';
        wait_cycles(2);
        check(led_target(0) = 255,
              "LED0 target should be 255 when host connected, got " & to_string(led_target(0)));
        host_conn <= '0';
        wait_cycles(2);
        check(led_target(0) = 0, "LED0 target back to 0 when host disconnects");

        report "=== Test 3: Armed state keeps fade_step 1, LED1 blinks ===";
        armed <= '1';
        wait_cycles(3);
        check(fade_step(0) = 1,
              "fade_step should be 1 in ARMED, got " & to_string(fade_step(0)));
        check(led_target(1) = 0 or led_target(1) = 255,
              "LED1 should be blinking in ARMED (0 or 255)");
        armed <= '0';
        wait_cycles(2);
        check(led_target(1) = 0, "LED1 off when not armed");

        report "=== Test 4: Capture sets sweep-bar fade_step 16 ===";
        armed <= '1';
        wait_cycles(2);
        capture_run <= '1';
        wait_cycles(3);
        check(fade_step(0) = 16,
              "fade_step should be 16 in CAPTURE, got " & to_string(fade_step(0)));
        -- Sweep bar: exactly two LEDs lit at a time
        check((led_target(0) + led_target(1) + led_target(2) + led_target(3) +
               led_target(4) + led_target(5) + led_target(6) + led_target(7)) = 510,
              "sweep bar should light exactly two LEDs");

        report "=== Test 5: Capture full holds DONE while run remains asserted ===";
        capture_full <= '1';
        wait_cycles(12);
        check(fade_step(0) = 1,
              "DONE must not re-enter CAPTURE while capture_full is asserted");
        capture_full <= '0';
        capture_run <= '0';
        armed <= '0';
        -- Two rising blink edges at BLINK_TOP=4, plus phase/state latency.
        wait_cycles(20);
        check(fade_step(0) = 1,
              "should return to fade_step=1 after DONE, got " & to_string(fade_step(0)));
        check(led_target(3) = 0, "DONE indicator should be off after return to IDLE");

        report "=== Test 6: Armed again after done ===";
        armed <= '1';
        wait_cycles(3);
        check(fade_step(0) = 1, "fade_step 1 in ARMED after done");

        if all_ok then
            report "=== ALL LED CONTROLLER TESTS PASSED ===";
        else
            report "=== SOME TESTS FAILED ===" severity failure;
        end if;
        std.env.finish;
        wait;
    end process;

end sim;
