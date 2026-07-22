# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 469 | 0.107 | 223.8 | 2.2 | 41.3 | 41.2 | 0.0 | 0.0 | ses_0f714ab7e9 |
| 1,000,000 | delta_rle | 650 | 0.077 | 217.8 | 2.2 | 31.1 | 30.2 | 0.8 | 0.0 | ses_029a8c478e |
| 10,000,000 | raw | 650 | 0.077 | 181.4 | 2.5 | 42.2 | 42.0 | 0.0 | 0.0 | ses_b3a6e157b7 |
| 10,000,000 | delta_rle | 650 | 0.077 | 160.4 | 1.6 | 21.3 | 20.6 | 0.6 | 0.0 | ses_d765a5abc7 |

Higher throughput means the hardware returned the capture faster for the same waveform window.