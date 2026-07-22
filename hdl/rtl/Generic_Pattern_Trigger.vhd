library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

-- Protocol-independent sampled pattern trigger.
--
-- Data_Channel_Mask selects the input bits that are appended on each sample
-- edge.  Selected channels are packed in ascending channel order.  The
-- module deliberately runs in the system/sample clock domain: an external
-- clock is detected as an edge on Clock_Channel, which makes the interface
-- safe to use with the already-synchronised capture inputs.
entity Generic_Pattern_Trigger is
  port (
    CLK               : in  std_logic;
    Inputs            : in  std_logic_vector(31 downto 0);
    Enable            : in  std_logic;
    Clock_Source      : in  std_logic; -- 0 = internal baud divider, 1 = external edge
    Clock_Edge        : in  std_logic; -- 0 = rising, 1 = falling
    Start_Mode        : in  std_logic; -- 0 = none, 1 = edge on Start_Channel
    Start_Channel     : in  natural range 0 to 31;
    Start_Polarity    : in  std_logic; -- asserted level: 0 = falling, 1 = rising
    Clock_Channel     : in  natural range 0 to 31;
    Data_Channel_Mask : in  std_logic_vector(31 downto 0);
    Baud_Div          : in  natural range 1 to 65535;
    Frame_Width       : in  natural range 1 to 32;
    Match_Value       : in  std_logic_vector(31 downto 0);
    Match_Mask        : in  std_logic_vector(31 downto 0);
    Bit_Order         : in  std_logic; -- 0 = LSB first, 1 = MSB first
    Trigger           : out std_logic := '0'
  );
end Generic_Pattern_Trigger;

architecture rtl of Generic_Pattern_Trigger is
begin
  process (CLK)
    variable prev_inputs : std_logic_vector(31 downto 0) := (others => '0');
    variable frame       : std_logic_vector(31 downto 0) := (others => '0');
    variable bit_count   : natural range 0 to 32 := 0;
    variable timer       : natural range 0 to 65535 := 0;
    variable selected    : natural range 0 to 32 := 0;
    variable pos         : natural range 0 to 31 := 0;
    variable lane        : natural range 0 to 31 := 0;
    variable width_mask  : std_logic_vector(31 downto 0) := (others => '0');
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
        if Start_Channel <= 31 then
          if Start_Polarity = '1' then
            start_edge := Inputs(Start_Channel) = '1' and
                         prev_inputs(Start_Channel) = '0';
          else
            start_edge := Inputs(Start_Channel) = '0' and
                         prev_inputs(Start_Channel) = '1';
          end if;
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
                           prev_inputs(Clock_Channel) = '0';
          else
            sample_edge := Inputs(Clock_Channel) = '0' and
                           prev_inputs(Clock_Channel) = '1';
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
          selected := 0;
          for i in 0 to 31 loop
            if Data_Channel_Mask(i) = '1' then
              selected := selected + 1;
            end if;
          end loop;

          if selected = 0 then
            started := false;
          else
            for i in 0 to 31 loop
              if Data_Channel_Mask(i) = '1' then
                lane := 0;
                for j in 0 to i loop
                  if Data_Channel_Mask(j) = '1' then
                    lane := lane + 1;
                  end if;
                end loop;
                lane := lane - 1;
                if bit_count + lane < Frame_Width then
                  if Bit_Order = '0' then
                    pos := bit_count + lane;
                  else
                    pos := Frame_Width - 1 - bit_count - lane;
                  end if;
                  frame(pos) := Inputs(i);
                end if;
              end if;
            end loop;

            if bit_count + selected >= Frame_Width then
              width_mask := (others => '0');
              for k in 0 to 31 loop
                if k < Frame_Width then
                  width_mask(k) := '1';
                end if;
              end loop;
              if unsigned((frame xor Match_Value) and Match_Mask and width_mask) = 0 then
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
              bit_count := bit_count + selected;
            end if;
          end if;
        end if;
      end if;
      prev_inputs := Inputs;
    end if;
  end process;
end rtl;
