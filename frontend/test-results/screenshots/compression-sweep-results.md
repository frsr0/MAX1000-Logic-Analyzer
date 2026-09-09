# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 49 | 1.02 | 230.0 | 2.3 | 43.9 | 43.7 | 0.0 | 0.0 | ses_3bff02d1a9 |
| 1,000,000 | delta_rle | 248 | 0.202 | 738.8 | 2.6 | 37.0 | 26.5 | 10.2 | 0.0 | ses_473c19265c |
| 10,000,000 | raw | 650 | 0.077 | 185.1 | 2.3 | 44.0 | 43.7 | 0.0 | 0.0 | ses_33cbd2d2fa |
| 10,000,000 | delta_rle | 248 | 0.202 | 668.6 | 2.3 | 29.3 | 19.9 | 9.3 | 0.0 | ses_33cbd2d2fa |

Higher throughput means the hardware returned the capture faster for the same waveform window.