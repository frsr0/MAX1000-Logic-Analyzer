# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 422 | 0.118 | 230.9 | 3.3 | 44.9 | 44.6 | 0.0 | 0.0 | ses_9bb9dd537e |
| 1,000,000 | delta_rle | 247 | 0.202 | 2763.6 | 1.5 | 36.2 | 26.2 | 9.8 | 0.0 | ses_9bb9dd537e |
| 10,000,000 | raw | 247 | 0.202 | 1762.4 | 1.6 | 45.3 | 45.0 | 0.0 | 0.0 | ses_2439492b50 |
| 10,000,000 | delta_rle | 3168 | 0.016 | 2721.7 | 2.6 | 28.9 | 20.4 | 8.4 | 0.0 | ses_d2133e21d2 |

Higher throughput means the hardware returned the capture faster for the same waveform window.