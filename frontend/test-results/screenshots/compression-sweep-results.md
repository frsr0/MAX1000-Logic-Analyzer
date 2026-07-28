# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 42 | 1.19 | 225.4 | 2.3 | 42.5 | 42.3 | 0.0 | 0.0 | ses_0c5ee1a5b6 |
| 1,000,000 | delta_rle | 654 | 0.076 | 251.4 | 2.2 | 66.4 | 18.5 | 47.8 | 47.8 | ses_a8dc189b93 |
| 10,000,000 | raw | 266 | 0.188 | 182.9 | 2.2 | 42.7 | 42.4 | 0.0 | 0.0 | ses_a8dc189b93 |
| 10,000,000 | delta_rle | 654 | 0.076 | 205.7 | 2.6 | 67.3 | 18.6 | 48.4 | 48.4 | ses_7a64558d1b |

Higher throughput means the hardware returned the capture faster for the same waveform window.