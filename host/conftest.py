from unittest.mock import MagicMock, patch
import sys, types
import pytest


# ── Shared tkinter mock (must be set before OLS_Console is imported) ──
_tk = types.ModuleType('tkinter')

class _FakeCanvas:
    def __init__(self, parent=None, **kw): self._children = []
    def delete(self, *a): pass
    def create_line(self, *a, **kw): return 0
    def create_rectangle(self, *a, **kw): return 0
    def create_text(self, *a, **kw): return 0
    def bind(self, *a, **kw): pass
    def winfo_width(self): return 500
    def winfo_height(self): return 200
    def winfo_children(self): return self._children
    def bbox(self, *a): return (0, 0, 10, 10)
    def coords(self, *a): pass
    def itemconfig(self, *a, **kw): pass
    def pack(self, **kw): pass
    def grid(self, **kw): pass
    def grid_remove(self): pass
    def grid_forget(self): pass
    def configure(self, **kw): pass
    def focus_set(self): pass
    def lift(self, *a): pass
    def lower(self, *a): pass

_tk.Canvas = _FakeCanvas
_tk.StringVar = MagicMock
_tk.BooleanVar = MagicMock
for _name in ('Frame','Label','Button','Checkbutton','Combobox',
              'Entry','Notebook','Separator','LabelFrame','Spinbox',
              'Scrollbar','PanedWindow','Progressbar','Tk','Text'):
    setattr(_tk, _name, MagicMock())
_tk.ttk = MagicMock()
_tk.filedialog = MagicMock()
_tk.messagebox = MagicMock()
sys.modules['tkinter'] = _tk
sys.modules['tkinter.ttk'] = _tk.ttk
sys.modules['tkinter.filedialog'] = _tk.filedialog
sys.modules['tkinter.messagebox'] = _tk.messagebox

def _make_smart_dev():
    d = MagicMock()
    d._data_available = True

    def get_qs():
        return 65536 if d._data_available else 0

    def do_read(q):
        d._data_available = False
        q = q if isinstance(q, int) and q > 0 else 65536
        return b'\x00' * min(q, 65536)

    def do_write(buf):
        d._data_available = True

    d.getQueueStatus.side_effect = get_qs
    d.read.side_effect = do_read
    d.write.side_effect = do_write
    return d


@pytest.fixture(autouse=True)
def mock_ftd2xx():
    with patch('ols_spi.ft', MagicMock()) as mock:
        mock.createDeviceInfoList.return_value = 0
        mock.open.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_dev():
    return _make_smart_dev()


@pytest.fixture
def ols(mock_dev):
    import ols_spi
    inst = ols_spi.OLS(speed_hz=12000000)
    inst.dev = mock_dev
    return inst


@pytest.fixture
def ols_no_dev():
    import ols_spi
    inst = ols_spi.OLS(speed_hz=12000000)
    inst.dev = None
    return inst


@pytest.fixture
def device_spi(ols):
    from ols_spi_device import OLSDeviceSPI
    inst = OLSDeviceSPI()
    inst.spi = ols
    return inst
