# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 948 | 0.264 | 603.3 | 2.1 | 212.8 | 212.0 | 0.0 | 0.0 | ses_c7187f8559 |
| 1,000,000 | delta_rle | 273 | 0.916 | 603.3 | 2.1 | 212.8 | 0.0 | 0.0 | 0.0 | ses_c7187f8559 |
| 10,000,000 | raw | 1161 | 0.215 | 375.5 | 1.7 | 215.5 | 214.9 | 0.0 | 0.0 | ses_3830ada2fe |
| 10,000,000 | delta_rle | 272 | 0.919 | 375.5 | 1.7 | 215.5 | 0.0 | 0.0 | 0.0 | ses_3830ada2fe |
| 50,000,000 | raw | 1163 | 0.215 | 352.6 | 2.2 | 211.6 | 211.1 | 0.0 | 0.0 | ses_955fd483f8 |
| 50,000,000 | delta_rle | 276 | 0.906 | 352.6 | 2.2 | 211.6 | 0.0 | 0.0 | 0.0 | ses_955fd483f8 |

Higher throughput means the hardware returned the capture faster for the same waveform window.