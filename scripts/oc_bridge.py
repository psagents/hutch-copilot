"""
oc_bridge.py — OpenCode ↔ hutch-python IPython bridge
======================================================
Canonical source (S3DF):
  hutch-copilot/scripts/oc_bridge.py

This script is written to the DAQ machine and executed inside a hutch-python
session. It starts a background thread that listens on TCP localhost:9999 for
JSON-encoded code requests from OpenCode running on S3DF.

Usage — install on the DAQ machine (run once per session from S3DF):

    ssh -o ConnectTimeout=10 -J psdev mfx-daq "cat > /tmp/oc_bridge.py" \
        < /sdf/home/f/fpoitevi/.claude/skills/hutch-copilot/scripts/oc_bridge.py

Then in the hutch-python session on the DAQ machine:

    exec(open('/tmp/oc_bridge.py').read())

Expected output: "OpenCode bridge listening on port 9999"

Calling the bridge from S3DF (SSH pipe — TCP port forwarding is blocked on DAQ):

    echo '{"code": "daq.status()"}' | \\
        ssh -o ConnectTimeout=10 -J psdev mfx-daq "python3 -c \\"
        import socket, json, sys
        s = socket.socket()
        s.connect(('localhost', 9999))
        s.sendall(sys.stdin.buffer.read())
        s.shutdown(socket.SHUT_WR)
        data = b''
        while True:
            chunk = s.recv(65536)
            if not chunk: break
            data += chunk
        resp = json.loads(data.decode())
        print(resp.get('output', resp.get('error', '')))
        \\""

Request format:   {"code": "<python expression or statements>"}
Response format:  {"status": "ok",    "output": "<captured stdout>"}
              or  {"status": "error", "error":  "<exception message>"}

Design notes:
  - exec(code, globals()): runs code in the hutch-python IPython globals so all
    pre-loaded device objects (yag0, mr1l4_homs, daq, beam_status, …) are visible,
    and functions defined in the submitted script can reference each other.
  - stdout is redirected to a StringIO during exec so print() output is captured
    and returned in the response rather than written to the hutch-python console.
  - The recv loop reads until the client closes its send side (SHUT_WR / EOF),
    allowing arbitrarily large code payloads.
  - One connection is handled at a time (serial); concurrent requests are queued
    by the OS up to the listen backlog of 5.
"""

import io
import json
import socket
import sys
import threading


def _oc_bridge():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("localhost", 9999))
    srv.listen(5)

    while True:
        conn, _ = srv.accept()
        try:
            # Read until client closes send side
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk

            req = json.loads(data.decode())
            code = req.get("code", "")

            # Capture stdout so print() output is returned to the caller
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            error = None
            try:
                # globals() here IS the hutch-python IPython namespace:
                # all pre-loaded devices are visible, and functions defined
                # within the submitted code can reference each other.
                exec(code, globals())  # noqa: S102
            except Exception as exc:
                error = str(exc)
            finally:
                sys.stdout = old_stdout

            if error:
                resp = json.dumps({"status": "error", "error": error})
            else:
                resp = json.dumps({"status": "ok", "output": buf.getvalue()})

            conn.sendall(resp.encode())

        except Exception as exc:
            try:
                conn.sendall(
                    json.dumps({"status": "error", "error": str(exc)}).encode()
                )
            except Exception:
                pass
        finally:
            conn.close()


threading.Thread(target=_oc_bridge, daemon=True).start()
print("OpenCode bridge listening on port 9999")
