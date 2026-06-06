library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity OLS_Logic_Analyzer_wrapper is
port (
    CLK       : IN  STD_LOGIC;
    UART_RX   : IN  STD_LOGIC;
    UART_TX   : INOUT STD_LOGIC;
    SPI_CS    : IN  STD_LOGIC := '1';
    SPI_MISO  : OUT STD_LOGIC := 'Z';
    MKR_D     : INOUT STD_LOGIC_VECTOR(14 downto 0);
    PMOD      : INOUT STD_LOGIC_VECTOR(7 downto 0);
    sdram_addr  : OUT STD_LOGIC_VECTOR(11 downto 0);
    sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_cas_n : OUT STD_LOGIC;
    sdram_cke   : OUT STD_LOGIC;
    sdram_cs_n  : OUT STD_LOGIC;
    sdram_dq    : INOUT STD_LOGIC_VECTOR(15 downto 0);
    sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_ras_n : OUT STD_LOGIC;
    sdram_we_n  : OUT STD_LOGIC;
    sdram_clk   : OUT STD_LOGIC;
    SEN_SDI     : INOUT STD_LOGIC;
    SEN_SPC     : INOUT STD_LOGIC;
    SEN_CS      : OUT   STD_LOGIC;
    SEN_SDO     : IN    STD_LOGIC;
    LED         : OUT STD_LOGIC_VECTOR(7 downto 0)
);
end OLS_Logic_Analyzer_wrapper;

architecture rtl of OLS_Logic_Analyzer_wrapper is
    attribute chip_pin : string;
    attribute chip_pin of CLK : signal is "H6";
    -- MKR digital pins D0-D14
    attribute chip_pin of MKR_D : signal is "H8,K10,H5,H4,J1,J2,L12,J12,J13,K11,K12,J10,H10,H13,G12";
    -- PMOD header
    attribute chip_pin of PMOD : signal is "M3,L3,M2,M1,N3,N2,K2,K1";
    -- AIN0-AIN6 + AIN7 are reserved by the ADC IP block (bank 1A)
    attribute chip_pin of LED : signal is "D8,C10,C9,B10,A10,A11,A9,A8";
    attribute chip_pin of sdram_addr : signal is "M10,N4,N8,M13,L10,N9,M11,N10,J8,N5,M5,K6";
    attribute chip_pin of sdram_ba : signal is "K8,N6";
    attribute chip_pin of sdram_cas_n : signal is "N7";
    attribute chip_pin of sdram_cke : signal is "M8";
    attribute chip_pin of sdram_clk : signal is "M9";
    attribute chip_pin of sdram_cs_n : signal is "M4";
    attribute chip_pin of sdram_dq : signal is "A12,B13,B12,C12,D12,E13,E12,F13,F8,G9,D9,E10,F9,F10,G10,D11";
    attribute chip_pin of sdram_dqm : signal is "F12,E9";
    attribute chip_pin of sdram_ras_n : signal is "M7";
    attribute chip_pin of sdram_we_n : signal is "K7";
    attribute chip_pin of SEN_CS : signal is "L5";
    attribute chip_pin of SEN_SDI : signal is "J7";
    attribute chip_pin of SEN_SDO : signal is "K5";
    attribute chip_pin of SEN_SPC : signal is "J6";
    attribute chip_pin of SPI_CS : signal is "A6";
    attribute chip_pin of SPI_MISO : signal is "B5";
    attribute chip_pin of UART_RX : signal is "A4";
    attribute chip_pin of UART_TX : signal is "B4";
    attribute io_standard : string;
    attribute io_standard of LED : signal is "2.5 V";
begin
    core : entity work.OLS_SDRAM_Top
    port map (
        CLK => CLK, UART_RX => UART_RX, UART_TX => UART_TX,
        SPI_CS => SPI_CS, SPI_MISO => SPI_MISO,
        MKR_D => MKR_D, PMOD => PMOD,
        LED => LED,
        sdram_addr => sdram_addr, sdram_ba => sdram_ba,
        sdram_cas_n => sdram_cas_n, sdram_cke => sdram_cke,
        sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
        sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
        sdram_we_n => sdram_we_n, sdram_clk => sdram_clk,
        SEN_SDI => SEN_SDI, SEN_SPC => SEN_SPC,
        SEN_CS => SEN_CS, SEN_SDO => SEN_SDO
    );
end rtl;
