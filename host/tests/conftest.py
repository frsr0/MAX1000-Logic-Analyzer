from unittest.mock import MagicMock
import sys, types

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
