export const PIN_OPTIONS = Array.from({ length: 23 }, (_, pin) => {
  if (pin <= 5) return { pin, label: `D${pin} (J1/${pin + 9})` };
  if (pin <= 14) return { pin, label: `D${pin} (J2/${pin - 5})` };
  const pmodPosition = pin - 14 + (pin >= 19 ? 2 : 0);
  return { pin, label: `PIO_${String(pin - 14).padStart(2, '0')} (PMOD/${pmodPosition})` };
});
