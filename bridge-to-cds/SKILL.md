---
name: bridge-to-cds
description: "Access LCLS /cds filesystem from S3DF. Use when reading hutch-python configs, happi DB, IOC files, presets, or any file that lives on the CDS/PCDS filesystem and is not mounted on S3DF. Also covers when EPICS CA access requires a controls machine."
---

# cds-bridge Skill

You help access files and resources that live on the **LCLS CDS/PCDS filesystem** (`/cds`) from
an S3DF session, by bridging through `psdev` via SSH.

---

## Filesystem Notes

### /cds is the canonical path — always use it

`/cds` and `/reg/neh` are **the same filesystem** (both NFS mounts of the same storage).
**Always use `/cds/...` paths** — `/reg/neh` is a legacy alias that still works but is deprecated.

Mapping:

| Legacy (`/reg/neh`) | Preferred (`/cds`) |
|---|---|
| `/reg/neh/home/<username>/` | `/cds/home/<first-letter>/<username>/` |
| `/reg/neh/operator/` | `/cds/home/opr/` (operator accounts) |
| `/reg/g/pcds/...` | `/cds/group/pcds/...` |

> When you see `/reg/...` in old documentation or scripts, mentally substitute `/cds/...`.
> When in doubt, check `/cds/` first — never probe both paths.

---

## Network Topology

Three distinct environments exist at LCLS. They are **not freely interoperable**:

| Environment | Example hosts | Filesystems | EPICS CA to IOCs |
|---|---|---|---|
| **S3DF** (batch/analysis) | `s3dflogin`, `sdf-login`, analysis nodes | `/sdf/`, `/sdf/data/`, `/sdf/group/` | **No** — firewalled |
| **CDS / psdev** (controls-adjacent) | `psdev`, `psusr`, `pslogin` | `/cds/` | **No** — psdev is read-only gateway |
| **Controls network** | `mfx-control`, `mfx-monitor`, `cxi-control`, etc. | `/cds/` mounted | **Yes** — full CA access |

Key constraint: **`/sdf` is NOT mounted on `psdev`**, and **`/cds` is NOT mounted on S3DF**.
The only bridge is SSH.

---

## Access Method: psdev SSH Bridge

Use this whenever you need to **read a file** that lives on `/cds` or `/reg` from an S3DF session.

### Pattern — pipe a Python script via stdin

```bash
ssh -o ConnectTimeout=10 psdev "python3 -" << 'PYEOF'
# Python 3.9 available; stdlib only (no conda, no pcdsdevices)
import json, pathlib

path = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
with open(path) as f:
    data = json.load(f)
print(len(data), "devices")
PYEOF
```

### Pattern — read a file directly

```bash
ssh -o ConnectTimeout=10 psdev "cat /cds/group/pcds/pyps/apps/hutch-python/mfx/conf.yml"
```

### Pattern — list a directory

```bash
ssh -o ConnectTimeout=10 psdev "ls /cds/group/pcds/pyps/apps/hutch-python/"
```

### Rules

- Always use `-o ConnectTimeout=10` to avoid hanging.
- Set a bash timeout of 30 s around the full call when the Python script may be slow.
- **Never reference `/sdf/...` paths inside the psdev command** — they are not mounted there.
- Python available on psdev: **Python 3.9**, stdlib only. No `happi`, no `pcdsdevices`, no conda.
- For JSON/YAML parsing, use `json` (stdlib) or `import yaml` — PyYAML is available.

---

## Key CDS Paths

### PCDS Software / Hutch-Python

| Path | What it is |
|---|---|
| `/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json` | **Happi device database** — flat JSON, all beamline devices |
| `/cds/group/pcds/pyps/apps/hutch-python/device_config/happi.cfg` | Happi config (points to `db.json`) |
| `/cds/group/pcds/pyps/apps/hutch-python/<hutch>/conf.yml` | Hutch-python session config (DAQ host, platform, db path, load list) |
| `/cds/group/pcds/pyps/apps/hutch-python/<hutch>/presets/` | Motor presets (YAML files, one per device) |
| `/cds/group/pcds/pyps/apps/hutch-python/<hutch>/experiments/` | Per-experiment config overrides |
| `/cds/group/pcds/pyps/apps/hutch-python/<hutch>/dev/` | Dev/staging hutch-python branch |

Valid `<hutch>` values: `mfx`, `mec`, `cxi`, `xcs`, `xpp`, `amo`, `rix`, `tmo`, `las`, `ued`, `tst`, `icl`, `common`, `det`

### Presets (motor saved positions)

Presets are YAML files under the hutch's `presets/` directory. Each file corresponds to one device.

```bash
ssh -o ConnectTimeout=10 psdev "ls /cds/group/pcds/pyps/apps/hutch-python/mfx/presets/"
ssh -o ConnectTimeout=10 psdev "cat /cds/group/pcds/pyps/apps/hutch-python/mfx/presets/mr1l4.yml"
```

### Home Directories

| Path | What it is |
|---|---|
| `/cds/home/<first-letter>/<username>/` | CDS home directories — e.g. `/cds/home/f/fpoitevi/`, `/cds/home/s/seaberg/` |
| `/cds/home/opr/` | Operator home directories (`mfxopr`, `cxiopr`, etc.) |

### EPICS / IOC configs

| Path | What it is |
|---|---|
| `/cds/group/pcds/epics/ioc/` | IOC top-level directories |
| `/cds/group/pcds/epics/base/` | EPICS base installations |
| `/cds/group/pcds/epics/extensions/` | EPICS extensions (caget, caput, camonitor binaries) |

> Note: `caget`/`caput` are **not** in the default PATH on `psdev`. They are available in
> `/cds/group/pcds/epics/extensions/bin/linux-x86_64/` but **CA connections will fail from psdev**
> because psdev is not on the controls network (172.21.x.x).

---

## EPICS CA Access — Requires a Controls Machine

If you need to `caget`/`caput` live PVs, the script must run on a hutch controls or monitor machine.

| Machine | Typical use |
|---|---|
| `mfx-control` | MFX hutch operator workstation — full CA access, hutch-python available |
| `mfx-monitor` | MFX monitoring workstation — CA access, lighter-weight |
| `cxi-control`, `xcs-control`, etc. | Other hutch control machines |

### Pattern — run a script on mfx-control

```bash
ssh -o ConnectTimeout=10 mfx-control "bash -s" << 'EOF'
source /cds/group/pcds/pyps/apps/hutch-python/mfx/mfxpython
python3 - << 'PYEOF'
from epics import caget
print(caget("MFX:MR1L4:PITCH"))
PYEOF
EOF
```

Or for a quick `caget`:

```bash
ssh -o ConnectTimeout=10 mfx-control \
  "/cds/group/pcds/epics/extensions/bin/linux-x86_64/caget MFX:MR1L4:PITCH"
```

> **Note:** `mfx-control` requires being on the SLAC VPN or on-site. S3DF SSH keys should work
> if your `~/.ssh/config` is set up for controls machines. If SSH fails from S3DF, try via psdev
> as a jump host: `ssh -J psdev mfx-control`.

---

## Decision Tree: Which Bridge to Use?

```
Need to read a file from /cds or /reg?
  └─► SSH to psdev, pipe Python or use cat/ls

Need to parse a config/JSON/YAML from /cds?
  └─► SSH to psdev, pipe Python (stdlib json/yaml)

Need to caget/caput a live PV?
  └─► SSH to mfx-control (or relevant hutch-control)
       └─► If that fails from S3DF: SSH -J psdev mfx-control

Need to run hutch-python commands (e.g. move a motor)?
  └─► SSH to mfx-control, source mfxpython, run Python
       OR: be physically at the hutch console

Need to run a psana2 analysis on S3DF data?
  └─► Stay on S3DF — /sdf/data/ is mounted here
```

---

## Common Recipes

### Read a hutch-python preset

```bash
ssh -o ConnectTimeout=10 psdev \
  "cat /cds/group/pcds/pyps/apps/hutch-python/mfx/presets/mr1l4.yml"
```

### List all MFX presets

```bash
ssh -o ConnectTimeout=10 psdev \
  "ls /cds/group/pcds/pyps/apps/hutch-python/mfx/presets/"
```

### Get happi entry for a device by name

```bash
ssh -o ConnectTimeout=10 psdev "python3 -" << 'PYEOF'
import json
DB = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
with open(DB) as f:
    db = json.load(f)
name = "im3l0"
v = db.get(name) or next((x for x in db.values() if x.get("name") == name), None)
if v:
    for k, val in sorted(v.items()):
        if val not in (None, "", [], {}):
            print(f"{k}: {val}")
PYEOF
```

### List all devices on a beamline sorted by z

```bash
ssh -o ConnectTimeout=10 psdev "python3 -" << 'PYEOF'
import json
DB = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
BEAMLINE = "MFX"  # uppercase
with open(DB) as f:
    db = json.load(f)
results = [v for v in db.values() if (v.get("beamline") or "").upper() == BEAMLINE]
results.sort(key=lambda v: float(v["z"]) if v.get("z") not in (None, "", "None") else float("inf"))
for v in results:
    z = v.get("z", "?")
    print(f"{str(z):>10}  {v['name']:<35}  {v.get('device_class','')}")
PYEOF
```

### Read a hutch conf.yml

```bash
ssh -o ConnectTimeout=10 psdev \
  "cat /cds/group/pcds/pyps/apps/hutch-python/mfx/conf.yml"
```

### Check if a file exists on /cds

```bash
ssh -o ConnectTimeout=10 psdev \
  "test -f /cds/group/pcds/pyps/apps/hutch-python/mfx/presets/mr1l4.yml && echo EXISTS || echo NOT FOUND"
```

---

## Gotchas

- **`/sdf` not mounted on psdev** — never use `/sdf/...` paths in psdev commands. Pipe everything via stdin.
- **No conda on psdev** — Python 3.9 stdlib only. No `happi`, `pcdsdevices`, `ophyd`, `epics`, `numpy`.
- **No EPICS CA from psdev** — `caget` is in PATH but connections to IOCs (172.21.x.x) are blocked.
- **SSH key auth** — passwordless SSH from S3DF to `psdev` should work; to `mfx-control` may require VPN or jump via psdev.
- **Heredoc quoting** — use `<< 'PYEOF'` (quoted) to prevent shell from expanding `$variables` in your Python code.
- **psdev is shared** — keep Python scripts short and non-blocking; do not run long jobs there.
- **File paths are case-sensitive** — beamline names in `db.json` are UPPERCASE (`MFX`, not `mfx`).
