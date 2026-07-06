# Compression sweep results

| rate Hz | codec | elapsed ms | throughput Msps | session |
| --- | --- | ---: | ---: | --- |
| 1,000,000 | raw | 939 | 0.266 | ses_a59dcb092e |
| 1,000,000 | delta | 1173 | 0.213 | ses_131c80b170 |
| 1,000,000 | rle | 1174 | 0.213 | ses_684f920e9e |
| 10,000,000 | raw | 1175 | 0.213 | ses_53bbf44205 |
| 10,000,000 | delta | 694 | 0.36 | ses_ff8d379f4b |
| 10,000,000 | rle | 694 | 0.36 | ses_c12a2f6093 |
| 50,000,000 | raw | 1179 | 0.212 | ses_3676ad6e8b |
| 50,000,000 | delta | 696 | 0.359 | ses_3427510ddb |
| 50,000,000 | rle | 772 | 0.324 | ses_63994f7dfc |

Higher throughput means the hardware returned the capture faster for the same waveform window.