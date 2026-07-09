# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 462 | 0.108 | 277.1 | 2.2 | 42.2 | 42.1 | 0.0 | 0.0 | ses_88043ed982 |
| 1,000,000 | delta_rle | 671 | 0.075 | 241.1 | 2.3 | 56.9 | 56.1 | 0.7 | 0.0 | ses_1177a1a484 |
| 10,000,000 | raw | 651 | 0.077 | 181.5 | 1.7 | 43.0 | 42.8 | 0.0 | 0.0 | ses_08d240bc26 |
| 10,000,000 | delta_rle | 655 | 0.076 | 196.4 | 2.2 | 56.5 | 55.5 | 0.9 | 0.0 | ses_b87534288d |

Higher throughput means the hardware returned the capture faster for the same waveform window.