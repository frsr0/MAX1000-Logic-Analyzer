# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 479 | 0.104 | 226.9 | 2.2 | 42.4 | 42.2 | 0.0 | 0.0 | ses_e92ace9a20 |
| 1,000,000 | delta_rle | 642 | 0.078 | 234.1 | 2.5 | 52.9 | 43.3 | 9.5 | 8.6 | ses_0bdc216cf1 |
| 10,000,000 | raw | 653 | 0.077 | 181.7 | 2.3 | 42.1 | 42.0 | 0.0 | 0.0 | ses_6e5d1ff616 |
| 10,000,000 | delta_rle | 659 | 0.076 | 163.0 | 1.6 | 25.3 | 24.5 | 0.7 | 0.0 | ses_45f3e27a56 |

Higher throughput means the hardware returned the capture faster for the same waveform window.