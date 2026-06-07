with open('hdl/rtl/spi_packet_rx.vhd', 'r') as f:
    c = f.read()

# Remove old report lines
import re
c = re.sub(r'report "CHECK_CRC[^;]*;', '', c)

# Add state entry reports - use unique pattern matching
# States are at the start of a line, inside a case statement
# Match: "            when STATE =>"
state_reports = {
    'WAIT_SYNC0': 'S0',
    'WAIT_SYNC1': 'S1', 
    'GET_CMD': 'CMD',
    'GET_SEQ': 'SEQ',
    'GET_LEN_L': 'LNL',
    'GET_LEN_H': 'LNH',
    'GET_CRC_L': 'CRL',
    'GET_CRC_H': 'CRH',
    'CHECK_CRC': 'CRC',
}

for state, label in state_reports.items():
    pattern = r'(            when {0} =>)$'.format(state)
    replacement = r'\1  report "{0}" severity note;'.format(label)
    c = re.sub(pattern, replacement, c)

with open('hdl/rtl/spi_packet_rx.vhd', 'w') as f:
    f.write(c)
print('Done')
