# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 483 | 0.104 | 226.4 | 2.3 | 42.3 | 42.1 | 0.0 | 0.0 | ses_bf4a272a85 |
| 1,000,000 | delta_rle | 651 | 0.077 | 216.6 | 1.4 | 33.1 | 32.2 | 0.8 | 0.0 | ses_ee4b8a872f |
| 10,000,000 | raw | 659 | 0.076 | 181.8 | 2.2 | 42.3 | 42.2 | 0.0 | 0.0 | ses_cf8ce76b40 |
| 10,000,000 | delta_rle | 657 | 0.076 | 162.1 | 2.6 | 24.3 | 23.4 | 0.8 | 0.0 | ses_6bce572919 |

Higher throughput means the hardware returned the capture faster for the same waveform window.