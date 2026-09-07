# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 433 | 0.115 | 223.3 | 1.3 | 45.9 | 45.7 | 0.0 | 0.0 | ses_ecb36cfa23 |
| 1,000,000 | delta_rle | 247 | 0.202 | 2769.4 | 1.4 | 46.7 | 20.9 | 25.7 | 16.3 | ses_ecb36cfa23 |
| 10,000,000 | raw | 645 | 0.078 | 186.0 | 1.6 | 42.7 | 42.5 | 0.0 | 0.0 | ses_5ef7242f42 |
| 10,000,000 | delta_rle | 2147 | 0.023 | 1692.1 | 1.9 | 29.9 | 20.4 | 9.4 | 0.0 | ses_002bef8000 |

Higher throughput means the hardware returned the capture faster for the same waveform window.