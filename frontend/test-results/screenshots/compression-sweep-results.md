# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1,000,000 | raw | 419 | 0.119 | 223.6 | 1.8 | 43.2 | 43.0 | 0.0 | 0.0 | ses_53da3ecf41 |
| 1,000,000 | delta_rle | 265 | 0.189 | 241.5 | 1.8 | 56.6 | 20.3 | 36.1 | 27.5 | ses_53da3ecf41 |
| 10,000,000 | raw | 247 | 0.202 | 2745.5 | 1.8 | 44.6 | 44.4 | 0.0 | 0.0 | ses_07eabe9b4a |
| 10,000,000 | delta_rle | 265 | 0.189 | 189.9 | 2.5 | 50.4 | 21.2 | 29.1 | 20.6 | ses_e352c9a818 |

Higher throughput means the hardware returned the capture faster for the same waveform window.