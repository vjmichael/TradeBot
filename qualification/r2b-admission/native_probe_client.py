"""Disposable synthetic GUI started by LightDM's real operator session.

This tests live PAM/session owner peer attestation. It does not exercise
provider tests, credentials, review approval or the product's UI workflow.
"""
import os
import socket
import tkinter as tk

if os.geteuid() == 0:
    raise SystemExit('operator_fixture_refused')
root=tk.Tk()
root.title('Maj Vaj — Native admission session qualification')
root.geometry('560x160+200+120')
label=tk.Label(root,text='Qualified operator session — no credentials or provider access',wraplength=520)
label.pack(padx=12,pady=35)
def ping():
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(8)
            s.connect('/run/maj-vaj-r2b-operator-probe.sock')
            data=s.recv(32)
        if data==b'ATTESTED\n':
            label.config(text='OS-authenticated PAM session; no provider authority')
        else:
            label.config(text='Session qualification refused')
    except OSError:
        label.config(text='Session qualification unavailable')
root.after(400,ping)
root.mainloop()
