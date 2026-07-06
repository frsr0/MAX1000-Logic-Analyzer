# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 953 | 0.262 | 594.4 | 1.8 | 209.0 | 208.5 | 0.0 | 0.0 | ses_2924841783 |
| 1,000,000 | delta | 1166 | 0.214 | 500.1 | 2.4 | 115.2 | 103.2 | 11.6 | 0.0 | ses_c5d1787b3f |
| 1,000,000 | rle | 1165 | 0.215 | 486.1 | 2.2 | 103.1 | 95.8 | 6.5 | 0.0 | ses_e075732f86 |
| 10,000,000 | raw | 1173 | 0.213 | 374.3 | 2.0 | 212.4 | 211.8 | 0.0 | 0.0 | ses_a28b35e25e |
| 10,000,000 | delta | 686 | 0.364 | 276.3 | 2.0 | 114.8 | 101.5 | 12.9 | 0.0 | ses_0905bd3654 |
| 10,000,000 | rle | 719 | 0.348 | 258.7 | 2.0 | 97.6 | 93.8 | 3.1 | 0.0 | ses_5893fedd4a |
| 50,000,000 | raw | 1162 | 0.215 | 352.7 | 2.2 | 208.5 | 208.0 | 0.0 | 0.0 | ses_090176ca4f |
| 50,000,000 | delta | 678 | 0.369 | 255.6 | 2.2 | 113.5 | 101.6 | 11.5 | 0.0 | ses_51d9151bec |
| 50,000,000 | rle | 681 | 0.367 | 240.8 | 2.2 | 98.5 | 93.5 | 4.5 | 0.0 | ses_52c05a51f5 |

Higher throughput means the hardware returned the capture faster for the same waveform window.