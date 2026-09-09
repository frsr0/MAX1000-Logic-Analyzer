# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 438 | 0.114 | 253.9 | 1.9 | 52.2 | 51.8 | 0.0 | 0.0 | ses_4e03f3efc7 |
| 1,000,000 | delta_rle | 684 | 0.073 | 224.2 | 1.1 | 48.9 | 31.0 | 17.6 | 0.0 | ses_ee883b2322 |
| 10,000,000 | raw | 260 | 0.192 | 709.4 | 10.5 | 51.8 | 51.5 | 0.0 | 0.0 | ses_ee883b2322 |
| 10,000,000 | delta_rle | 659 | 0.076 | 195.2 | 2.0 | 34.7 | 21.0 | 13.6 | 0.0 | ses_0936f72e54 |

Higher throughput means the hardware returned the capture faster for the same waveform window.