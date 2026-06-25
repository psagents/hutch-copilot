# /ami-plot — Create or Edit AMI Online Monitoring Graphs

Triggered by: `/ami-plot`, "create an AMI plot", "add an AMI graph", "online
monitoring", "plot correlation", "ami correlation", "ami histogram", "ami timeseries".

This command helps the user build or modify AMI (Analysis Monitoring Interface)
graphs for live online monitoring during a run. It uses `@ask-ami` for the underlying
graph node implementation details.

---

## Phase 1: Parse the Request

Extract from the user's message:
- **Plot type**: correlation, histogram, timeseries, image, binned average
- **X signal** (for correlations): detector name, IPM, PV name
- **Y signal**: detector name, IPM, PV name, or computed quantity
- **Filters**: shot selection condition (e.g. "only hits", "laser on shots")
- **Accumulation**: number of shots to average or display

Examples:
- "/ami-plot correlation between IPM2 and IPM1" → correlation scatter plot
- "/ami-plot timeseries of wave8 sum" → rolling timeseries
- "/ami-plot histogram of epix10k2M total ADU" → shot-by-shot histogram
- "/ami-plot mean image of epix10k2M" → accumulated mean image

---

## Phase 2: Map to AMI Node Types

Translate the request into the AMI computation graph structure. Use `@ask-ami`
to get the specific node implementation details if any of these are unclear.

| Plot type | AMI nodes involved |
|---|---|
| Correlation (X vs Y) | Source × 2 → `Correlation` → `ScatterPlot` |
| Histogram | Source → `Histogram` → `HistogramPlot` |
| Timeseries | Source → `TimeSeries` → `LinePlot` |
| Mean image | Source (detector) → `Accumulator` (mean) → `ImagePlot` |
| Binned average | Source (sig) + Source (bin) → `BinnedAverage` → `LinePlot` |
| ROI sum | Source (detector) → `ROI` → (timeseries or histogram) |

**Worker vs. Collector colors:**
- Per-event operations (ROI, threshold filter, shot selection): `worker` color
- Reduction across shots (mean, histogram, correlation): `localCollector` → `globalCollector`

---

## Phase 3: Identify Data Sources

Map signal names to AMI data sources. Use the session state for detector names and
the hutch reference for PV and detector aliases.

| Signal type | AMI source pattern |
|---|---|
| psana2 detector | `Detector('epix10k2m')` or the psana alias confirmed in the experiment |
| EPICS PV | `Epics('MFX:USR:MMS:01:RBV')` or similar |
| IPM (beam intensity) | `Epics('MFX:DG2:IPM:CH0_SUM')` or psana IPM alias |
| Waveform digitizer | `Detector('wave8')` (psana alias) |

If the user gives informal names (e.g., "IPM2", "the main detector"), translate them
using the hutch reference and session state. Confirm the alias if uncertain.

---

## Phase 4: Generate Setup Instructions

Present clear step-by-step instructions for the user to add the graph in the AMI GUI.
AMI is graphical — the user adds nodes by dragging from the node library.

**Example: Correlation plot (IPM2 vs IPM1)**

```
To create an IPM2 vs IPM1 correlation plot in AMI:

1. In the AMI flowchart GUI, add a Source node:
   - Type: Epics  
   - PV: MFX:DG2:IPM:CH0_SUM  (IPM2)
   - Name: ipm2

2. Add a second Source node:
   - Type: Epics
   - PV: MFX:DG1:IPM:CH0_SUM  (IPM1)
   - Name: ipm1

3. Add a Correlation node:
   - Connect: ipm2 → x input
   - Connect: ipm1 → y input
   - Color: globalCollector
   - N points: 500 (or your preference)

4. Add a ScatterPlot node:
   - Connect: Correlation → input
   - Title: "IPM2 vs IPM1"

5. Save the graph (Ctrl+S or File → Save).
   The plot will update live as shots come in.
```

---

## Phase 5: AMI Configuration File (Optional)

If the user wants to save the graph as a configuration file for reuse, use `@ask-ami`
to look up the graph serialization format and show the equivalent JSON/YAML configuration
that can be loaded via AMI's file → load graph function.

---

## Notes

- AMI graphs run on the AMI workers alongside the live DAQ data stream.
- Changes to the graph take effect immediately — no restart needed.
- For complex custom nodes (not in the standard library), use `@ask-ami` to look up
  whether there's an existing implementation or how to write one.
- AMI is separate from SmallData — it does online monitoring, not persistent HDF5 writing.
  For offline analysis, use `/smd-config` → `@ask-lute`.
