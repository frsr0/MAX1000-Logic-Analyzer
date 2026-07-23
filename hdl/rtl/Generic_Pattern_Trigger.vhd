library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

-- Protocol-independent sampled pattern trigger.
--
-- Data_Channel_0..3 are compact selectors for the input bits that are
-- appended on each sample edge. Data_Lane_Count chooses 1..4 of them. The
-- module deliberately runs in the system/sample clock domain: an external
-- clock is detected as an edge on Clock_Channel, which makes the interface
-- safe to use with the already-synchronised capture inputs.
entity Generic_Pattern_Trigger is
  port (
    CLK               : in  std_logic;
    Inputs            : in  std_logic_vector(15 downto 0);
    Enable            : in  std_logic;
    Clock_Source      : in  std_logic; -- 0 = internal baud divider, 1 = external edge
    Clock_Edge        : in  std_logic; -- 0 = rising, 1 = falling
    Start_Mode        : in  std_logic; -- 0 = none, 1 = edge on Start_Channel
    Start_Channel     : in  natural range 0 to 15;
    Start_Polarity    : in  std_logic; -- asserted level: 0 = falling, 1 = rising
    Clock_Channel     : in  natural range 0 to 15;
    Data_Lane_Count   : in  natural range 1 to 4;
    Data_Channel_0    : in  natural range 0 to 15;
    Data_Channel_1    : in  natural range 0 to 15;
    Data_Channel_2    : in  natural range 0 to 15;
    Data_Channel_3    : in  natural range 0 to 15;
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
    variable prev_inputs : std_logic_vector(15 downto 0) := (others => '0');
    variable frame       : std_logic_vector(31 downto 0) := (others => '0');
    variable bit_count   : natural range 0 to 32 := 0;
    variable timer       : natural range 0 to 65535 := 0;
    variable selected    : natural range 1 to 4 := 1;
    variable sample_word : std_logic_vector(3 downto 0) := (others => '0');
    variable packed_sample : std_logic_vector(3 downto 0) := (others => '0');
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
                       prev_inputs(Start_Channel) = '0';
        else
          start_edge := Inputs(Start_Channel) = '0' and
                       prev_inputs(Start_Channel) = '1';
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
          selected := Data_Lane_Count;
          sample_word(0) := Inputs(Data_Channel_0);
          sample_word(1) := Inputs(Data_Channel_1);
          sample_word(2) := Inputs(Data_Channel_2);
          sample_word(3) := Inputs(Data_Channel_3);

          -- The compare register is a fixed left-shifting register. The
          -- selected lanes are reversed into the appended word so that the
          -- register's MSB-to-LSB order is always the wire order. For
          -- LSB-first protocols the host reverses the configured value/mask
          -- within Frame_Width before writing the registers. The host also
          -- clears bits above Frame_Width; keeping both normalizations out of
          -- this datapath is what keeps the implementation small.
          packed_sample := (others => '0');
          case selected is
            when 1 => packed_sample(0) := sample_word(0);
            when 2 => packed_sample(1) := sample_word(0);
                     packed_sample(0) := sample_word(1);
            when 3 => packed_sample(2) := sample_word(0);
                     packed_sample(1) := sample_word(1);
                     packed_sample(0) := sample_word(2);
            when others => packed_sample(3) := sample_word(0);
                           packed_sample(2) := sample_word(1);
                           packed_sample(1) := sample_word(2);
                           packed_sample(0) := sample_word(3);
          end case;

          case selected is
            when 1 => frame := std_logic_vector(shift_left(unsigned(frame), 1));
                      frame(0) := packed_sample(0);
            when 2 => frame := std_logic_vector(shift_left(unsigned(frame), 2));
                      frame(1 downto 0) := packed_sample(1 downto 0);
            when 3 => frame := std_logic_vector(shift_left(unsigned(frame), 3));
                      frame(2 downto 0) := packed_sample(2 downto 0);
            when others => frame := std_logic_vector(shift_left(unsigned(frame), 4));
                           frame(3 downto 0) := packed_sample(3 downto 0);
          end case;

          if bit_count + selected >= Frame_Width then
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
            bit_count := bit_count + selected;
          end if;
        end if;
      end if;
      prev_inputs := Inputs;
    end if;
  end process;
end rtl;
