// Exports: CSV/JSON/VCD/NPZ/HTML report + PNG screenshot of the canvas.
import { downloadExport } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

export function ExportPanel() {
  const { activeSession, toast } = useApp();
  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  const dl = async (format: string, body: unknown = {}) => {
    try {
      await downloadExport(activeSession.id, format, body);
      toast('success', `${format.toUpperCase()} export downloaded`);
    } catch (e: any) {
      toast('error', `Export failed: ${e.message}`);
    }
  };

  const screenshot = () => {
    const canvas = document.querySelector<HTMLCanvasElement>('.waveform-canvas');
    if (!canvas) { toast('warning', 'No waveform canvas visible'); return; }
    canvas.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${activeSession.name.replace(/\W+/g, '_')}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  };

  const sel = waveformView.selectionStart !== null && waveformView.selectionEnd !== null
    ? [Math.floor(Math.min(waveformView.selectionStart, waveformView.selectionEnd)),
       Math.ceil(Math.max(waveformView.selectionStart, waveformView.selectionEnd))]
    : null;

  return (
    <div className="panel-body">
      <div className="export-grid">
        <button onClick={() => dl('csv', { start: 0, end: -1 })}>Raw samples CSV</button>
        {sel && <button onClick={() => dl('csv', { start: sel[0], end: sel[1] })}>Selection CSV</button>}
        <button onClick={() => dl('json', { include_raw: true })}>JSON session</button>
        <button onClick={() => dl('vcd', {})}>VCD (digital)</button>
        <button onClick={() => dl('npz')}>NumPy NPZ</button>
        <button onClick={() => dl('report')}>HTML report</button>
        <button onClick={screenshot}>PNG screenshot</button>
        {activeSession.decoders.filter((d) => d.status === 'done').map((d) => (
          <button key={d.id}
            onClick={() => dl('csv', { decoder_instance: d.id })}>
            Decoded CSV: {d.name || d.decoder_id}
          </button>
        ))}
      </div>
      <h4>Export history</h4>
      <table className="data-table">
        <thead><tr><th>when</th><th>format</th><th>file</th></tr></thead>
        <tbody>
          {[...activeSession.exports].reverse().slice(0, 12).map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.timestamp * 1000).toLocaleTimeString()}</td>
              <td>{e.format}</td>
              <td className="mono">{e.filename}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
