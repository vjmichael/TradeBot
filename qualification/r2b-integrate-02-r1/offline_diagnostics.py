"""Independent local graphical diagnostics; stays responsive without Internet.

Requires local graphical desktop. Does not make unreachable remote web UI load.
No credentials, root privilege, restart or network request.
"""
import queue
import threading
import tkinter as tk
from offline_health import probe_local

class OfflineDiagnostics:
    def __init__(self, root, probe=probe_local):
        self.root = root
        self.probe = probe
        self.result = queue.SimpleQueue()
        self.inflight = False
        self.closed = False
        root.title('Maj Vaj — Connection and service status')
        self.network = tk.StringVar(value='Internet status unknown')
        self.services = tk.StringVar(value='Checking services')
        self.detail = tk.StringVar(value='Local diagnostics work without internet access.')
        wrapper = tk.Frame(root, padx=16, pady=16)
        wrapper.pack(fill='both', expand=True)
        tk.Label(wrapper, textvariable=self.network, anchor='w').pack(fill='x')
        tk.Label(wrapper, textvariable=self.services, anchor='w', font=('TkDefaultFont', 11, 'bold')).pack(fill='x')
        tk.Label(wrapper, textvariable=self.detail, justify='left', anchor='w', wraplength=450).pack(fill='x')
        tk.Button(wrapper, text='Check local services', command=self.refresh).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = root.after(100, self.poll)
        self.refresh()

    def refresh(self):
        if self.inflight or self.closed: return
        self.inflight = True
        def worker():
            try: self.result.put(self.probe())
            except Exception: self.result.put(None)
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        if self.closed: return
        try:
            item = self.result.get_nowait()
        except queue.Empty:
            pass
        else:
            self.inflight = False
            if item is None:
                self.services.set('Checking services')
                self.detail.set('Local health information is unavailable.')
            else:
                self.services.set(item.summary())
                self.network.set('No network route' if item.internet == 'no_network_route' else 'Internet status unknown')
                parts = [label.replace('_',' ').title()+': '+value for label,value in item.services.items()]
                self.detail.set('Local systemd state only (not provider health): '+', '.join(parts))
        self.poll_id = self.root.after(250, self.poll)

    def close(self):
        self.closed = True
        try:
            self.root.after_cancel(self.poll_id)
        except (tk.TclError, AttributeError):
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    OfflineDiagnostics(root)
    root.mainloop()

if __name__ == '__main__': main()