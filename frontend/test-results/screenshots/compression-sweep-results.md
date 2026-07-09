# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 474 | 0.105 | 269.3 | 2.2 | 41.5 | 41.3 | 0.0 | 0.0 | ses_94fad6fdf1 |
| 1,000,000 | delta_rle | 654 | 0.076 | 242.0 | 1.2 | 58.3 | 45.8 | 12.4 | 10.5 | ses_e8fcb266c5 |
| 10,000,000 | raw | 660 | 0.076 | 180.9 | 2.2 | 41.5 | 41.4 | 0.0 | 0.0 | ses_22de4658e7 |
| 10,000,000 | delta_rle | 664 | 0.075 | 210.3 | 1.6 | 71.9 | 58.9 | 12.9 | 11.0 | ses_cc3611765e |

Higher throughput means the hardware returned the capture faster for the same waveform window.