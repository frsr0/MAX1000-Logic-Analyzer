library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity tb_led_controller is
end tb_led_controller;

architecture sim of tb_led_controller is
    constant CLK_PERIOD : time := 20.833 ns;
    signal clk             : std_logic := '0';
    signal rst             : std_logic := '0';
    signal armed           : std_logic := '0';
    signal capture_run     : std_logic := '0';
    signal capture_full    : std_logic := '0';
    signal continuous_mode : std_logic := '0';
    signal generator_tx    : std_logic := '0';
    signal generator_rx    : std_logic := '0';
    signal led             : std_logic_vector(7 downto 0);

    procedure wait_cycles(n : natural) is
    begin
        for i in 1 to n loop
            wait until rising_edge(clk);
        end loop;
        wait for 0 fs;
    end procedure;
begin
    clk <= not clk after CLK_PERIOD / 2;

    DUT: entity work.LED_Controller
        port map (
            clk => clk, rst => rst, armed => armed,
            capture_run => capture_run, capture_full => capture_full,
            continuous_mode => continuous_mode,
            generator_tx => generator_tx, generator_rx => generator_rx,
            led => led
        );

    process
        procedure check(cond : boolean; msg : string) is
        begin
            assert cond report msg severity failure;
        end procedure;
    begin
        rst <= '1';
        wait until rising_edge(clk);
        rst <= '0';
        wait_cycles(2);

        check(led = "00000001", "idle should start with a single comet head");

        armed <= '1';
        wait_cycles(1);
        check(led = "01010101", "pre-trigger should show its checkerboard");

        capture_run <= '1';
        wait_cycles(1);
        check(led = "00000011", "finite capture should show a two-LED scanner");

        continuous_mode <= '1';
        wait_cycles(1);
        check(led = "00010001", "live capture should show opposing heads");

        continuous_mode <= '0';
        capture_run <= '0';
        armed <= '0';
        generator_tx <= '1';
        wait_cycles(1);
        check(led = "00000111", "TX should show a three-LED packet");

        generator_tx <= '0';
        generator_rx <= '1';
        wait_cycles(1);
        generator_rx <= '0';
        wait_cycles(1);
        -- The animation frame is deliberately slow (the position comes from
        -- timebase(25 downto 23)), so this short event remains at the initial
        -- right-to-left packet shape during the focused test.
        check(led = "00000111", "RX event should latch the receive packet");

        capture_full <= '1';
        wait_cycles(1);
        check(led = "11111111", "completion should light the whole array");

        report "=== ALL LED CONTROLLER TESTS PASSED ===";
        std.env.finish;
        wait;
    end process;
end sim;
