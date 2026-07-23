library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

-- Protocol-independent sampled pattern trigger.
--
-- Data_Channel_0 is the compact selector for the input bit that is appended
-- on each sample edge. Multi-channel patterns are refined in software after
-- capture; the FPGA provides a selectable coarse trigger channel. The
-- module deliberately runs in the system/sample clock domain: an external
-- clock is detected as an edge on Clock_Channel, which makes the interface
-- safe to use with the already-synchronised capture inputs.
entity Generic_Pattern_Trigger is
  port (
    CLK               : in  std_logic;
    Inputs            : in  std_logic_vector(15 downto 0);
    Prev_Inputs       : in  std_logic_vector(15 downto 0);
    Enable            : in  std_logic;
    Clock_Source      : in  std_logic; -- 0 = internal baud divider, 1 = external edge
    Clock_Edge        : in  std_logic; -- 0 = rising, 1 = falling
    Start_Mode        : in  std_logic; -- 0 = none, 1 = edge on Start_Channel
    Start_Channel     : in  natural range 0 to 15;
    Start_Polarity    : in  std_logic; -- asserted level: 0 = falling, 1 = rising
    Clock_Channel     : in  natural range 0 to 15;
    Data_Channel_0    : in  natural range 0 to 15;
    Baud_Div          : in  natural range 1 to 65535;
    Frame_Width       : in  natural range 1 to 32;
    Match_Value       : in  std_logic_vector(31 downto 0);
    Match_Mask        : in  std_logic_vector(31 downto 0);
    -- Register-compatibility indicator. The host normalizes LSB-first values
    -- into the fixed left-shift compare order before writing the registers.
    Bit_Order         : in  std_logic; -- 0 = LSB first, 1 = MSB first
    Trigger           : out std_logic := '0'
  );
end Generic_Pattern_Trigger;

architecture rtl of Generic_Pattern_Trigger is
begin
  process (CLK)
    variable frame       : std_logic_vector(31 downto 0) := (others => '0');
    variable bit_count   : natural range 0 to 32 := 0;
    variable timer       : natural range 0 to 65535 := 0;
    variable sample_edge : boolean;
    variable start_edge  : boolean;
    variable started     : boolean := false;
    variable waiting     : boolean := false;
  begin
    if rising_edge(CLK) then
      Trigger <= '0';

      if Enable = '0' then
        frame := (others => '0');
        bit_count := 0;
        timer := 0;
        started := false;
        waiting := false;
      else
        start_edge := false;
        if Start_Polarity = '1' then
          start_edge := Inputs(Start_Channel) = '1' and
                       Prev_Inputs(Start_Channel) = '0';
        else
          start_edge := Inputs(Start_Channel) = '0' and
                       Prev_Inputs(Start_Channel) = '1';
        end if;

        if Start_Mode = '1' and not started and start_edge then
          started := true;
          timer := 0;
          waiting := Clock_Source = '0';
        elsif Start_Mode = '0' then
          started := true;
        end if;

        sample_edge := false;
        if Clock_Source = '1' then
          if Clock_Edge = '0' then
            sample_edge := Inputs(Clock_Channel) = '1' and
                           Prev_Inputs(Clock_Channel) = '0';
          else
            sample_edge := Inputs(Clock_Channel) = '0' and
                           Prev_Inputs(Clock_Channel) = '1';
          end if;
        elsif started then
          -- A start-qualified internal-baud frame begins at the centre of the
          -- start bit, then waits one full bit before sampling data bit 0.
          -- This preserves the UART alignment of the legacy trigger.
          if waiting then
            if Baud_Div < 2 then
              timer := 0;
              waiting := false;
            elsif timer = (Baud_Div / 2) - 1 then
              timer := 0;
              waiting := false;
            else
              timer := timer + 1;
            end if;
          elsif Baud_Div < 2 then
            timer := 0;
            sample_edge := true;
          elsif timer = Baud_Div - 1 then
            timer := 0;
            sample_edge := true;
          else
            timer := timer + 1;
          end if;
        end if;

        if started and sample_edge then
          -- This is intentionally a single-lane coarse matcher. When the
          -- configured pattern has more channels, the host projects it onto
          -- Data_Channel_0 and the backend refines the full capture.
          frame := std_logic_vector(shift_left(unsigned(frame), 1));
          frame(0) := Inputs(Data_Channel_0);

          -- The compare register is a fixed left-shifting register. The
          -- The register's MSB-to-LSB order is always the wire order. For
          -- LSB-first protocols the host reverses the configured value/mask
          -- within Frame_Width before writing the registers. The host also
          -- clears bits above Frame_Width; keeping both normalizations out of
          -- this datapath is what keeps the implementation small.
          if bit_count + 1 >= Frame_Width then
            if unsigned((frame xor Match_Value) and Match_Mask) = 0 then
              Trigger <= '1';
            end if;
            frame := (others => '0');
            bit_count := 0;
            -- A start-qualified frame waits for the next start condition;
            -- a free-running matcher continues with the next frame.
            if Start_Mode = '1' then
              started := false;
            end if;
          else
            bit_count := bit_count + 1;
          end if;
        end if;
      end if;
    end if;
  end process;
end rtl;
