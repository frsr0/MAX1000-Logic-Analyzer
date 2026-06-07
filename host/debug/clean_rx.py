with open('hdl/rtl/spi_packet_rx.vhd', 'r') as f:
    lines = f.readlines()
lines = [l for l in lines if 'report ' not in l]
with open('hdl/rtl/spi_packet_rx.vhd', 'w') as f:
    f.writelines(lines)
print('Cleaned')
