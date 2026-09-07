# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 443 | 0.113 | 238.8 | 1.5 | 56.8 | 56.2 | 0.0 | 0.0 | ses_b1a7b2a0f3 |
| 1,000,000 | delta_rle | 679 | 0.074 | 301.1 | 1.3 | 112.6 | 37.2 | 75.0 | 56.7 | ses_abee54ee10 |
| 10,000,000 | raw | 656 | 0.076 | 207.6 | 1.8 | 50.8 | 50.5 | 0.0 | 0.0 | ses_73476ef3ee |
| 10,000,000 | delta_rle | 665 | 0.075 | 183.0 | 2.1 | 39.9 | 21.1 | 18.5 | 0.0 | ses_342aeeac15 |

Higher throughput means the hardware returned the capture faster for the same waveform window.