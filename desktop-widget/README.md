# Windows desktop GPU widget

A read-only desktop client for GPU Hot hub WebSocket data. Displays one card per GPU, three columns, with utilization and VRAM percentage rings. Opaque, DPI-aware window; supersampled rings. No changes to GPU Hot nodes or hub are required.

## Run

Requires Windows, Python 3.13 with tkinter, and access to the hub:

```powershell
python -m pip install -r requirements.txt
pythonw gpu_widget.pyw
```

Set `HUB_URL` and `NODES` near the top of `gpu_widget.pyw` for your deployment. The current defaults target a private Tailscale hub and nodes 92/93/95; other users must replace these values. Connections intentionally bypass HTTP proxies.

Left-drag to move. Right-click for Always on top or Exit. Position and pin preference are stored beside the script in `state.json`; this device-local file must not be committed. The widget uses local TCP port 47131 as a single-instance guard. Network failures reconnect automatically; a missing frame for 10 seconds marks the header disconnected. Last GPU readings remain visible while disconnected and should not be treated as current.

For sign-in startup, create a Windows Startup-folder shortcut targeting your `pythonw.exe`, with the absolute script path as its argument. No startup registration is performed by the script itself.

## Change notes

- Add six-GPU English 2-by-3 desktop display backed by the hub stream.
- Remove whole-window transparency, enable Per-Monitor V2 DPI awareness, and use Pillow supersampling for ring edges.
