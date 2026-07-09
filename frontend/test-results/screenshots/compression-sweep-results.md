# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 450 | 0.111 | 224.9 | 1.8 | 42.1 | 41.9 | 0.0 | 0.0 | ses_056c4b72f0 |
| 1,000,000 | delta_rle | 656 | 0.076 | 254.5 | 2.4 | 66.2 | 54.7 | 11.3 | 10.4 | ses_a1e3ea84ea |
| 10,000,000 | raw | 648 | 0.077 | 181.9 | 2.1 | 42.2 | 42.0 | 0.0 | 0.0 | ses_7e8381ebf8 |
| 10,000,000 | delta_rle | 650 | 0.077 | 206.8 | 2.7 | 65.2 | 56.2 | 8.9 | 8.0 | ses_21f37b440e |

Higher throughput means the hardware returned the capture faster for the same waveform window.