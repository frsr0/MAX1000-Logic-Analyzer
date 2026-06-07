import re

with open('hdl/tb/tb_spi_protocol.vhd', 'r') as f:
    c = f.read()

# Replace polling loop with fixed wait
old_poll = "for i in 0 to 200 loop\n      exit when rx_ok = '1' or rx_err = '1';\n      wait for CLK_PERIOD;\n    end loop;\n    wait for 0 ps;"
c = c.replace(old_poll, 'wait for 200 ns;')

# Replace all 'wait until rx_ok ...' patterns
c = re.sub(r"wait until rx_ok = '1' or rx_err = '1' for 1 us;", 'wait for 200 ns;', c)

# Replace rx_ok checks with latched versions  
c = c.replace("if rx_ok = '1' and rx_cmd", "if rx_ok_latched = '1' and rx_cmd")
c = c.replace("if rx_err = '1' then", "if rx_err_latched = '1' then")
c = c.replace("rx_err = '1' then", "rx_err_latched = '1' then")

with open('hdl/tb/tb_spi_protocol.vhd', 'w') as f:
    f.write(c)
print('Done')
