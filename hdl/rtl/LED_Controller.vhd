library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Direct-drive eight-LED activity controller.  This intentionally avoids the
-- former eight-channel PWM/fade bank: the MAX 10 is almost full and visual
-- status must not consume capture-path routing or enough LEs to prevent fit.
entity LED_Controller is
    port (
        clk             : in  std_logic;
        rst             : in  std_logic := '0';
        armed           : in  std_logic;
        capture_run     : in  std_logic;
        capture_full    : in  std_logic;
        continuous_mode : in  std_logic := '0';
        generator_tx    : in  std_logic := '0';
        generator_rx    : in  std_logic := '0'; -- one-clock byte event
        led             : out std_logic_vector(7 downto 0) := (others => '0')
    );
end LED_Controller;

architecture rtl of LED_Controller is
    -- 100 MHz: frame position changes every ~84 ms and blink phase every
    -- ~335 ms. One shared counter replaces several independent wide timers.
    signal timebase      : unsigned(25 downto 0) := (others => '0');
    signal rx_hold_frames : natural range 0 to 7 := 0;

    function rotate_l(value : std_logic_vector(7 downto 0); pos : natural)
        return std_logic_vector is
    begin
        return std_logic_vector(rotate_left(unsigned(value), pos));
    end function;

    function rotate_r(value : std_logic_vector(7 downto 0); pos : natural)
        return std_logic_vector is
    begin
        return std_logic_vector(rotate_right(unsigned(value), pos));
    end function;
begin
    process(clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                timebase <= (others => '0');
                rx_hold_frames <= 0;
            else
                timebase <= timebase + 1;

                -- Hold a received byte for seven animation frames (~0.6 s).
                if generator_rx = '1' then
                    rx_hold_frames <= 7;
                elsif timebase(22 downto 0) = 0 and rx_hold_frames > 0 then
                    rx_hold_frames <= rx_hold_frames - 1;
                end if;
            end if;
        end if;
    end process;

    process(timebase, capture_full, generator_rx, rx_hold_frames, generator_tx,
            continuous_mode, capture_run, armed)
        variable position : natural range 0 to 7;
    begin
        position := to_integer(timebase(25 downto 23));
        -- Highest-priority pattern wins, so activity remains unambiguous.
        if capture_full = '1' then
            if timebase(25) = '0' then
                led <= (others => '1');       -- capture complete flash
            else
                led <= (others => '0');
            end if;
        elsif generator_rx = '1' or rx_hold_frames > 0 then
            led <= rotate_r("00000111", position); -- RX packet: right-to-left
        elsif generator_tx = '1' then
            led <= rotate_l("00000111", position); -- TX packet: left-to-right
        elsif continuous_mode = '1' and capture_run = '1' then
            led <= rotate_l("00010001", position); -- opposing live heads
        elsif capture_run = '1' then
            led <= rotate_l("00000011", position); -- finite capture scanner
        elsif armed = '1' then
            if timebase(25) = '0' then
                led <= "01010101";            -- pre-trigger checkerboard
            else
                led <= "10101010";
            end if;
        else
            led <= rotate_l("00000001", position); -- idle comet head
        end if;
    end process;
end rtl;
