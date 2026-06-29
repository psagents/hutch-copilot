# /align-beam — Beam Alignment Optimization

Triggered by: `/align-beam`, "align the beam", "optimize beam position",
"run the optimization", "beam is not aligned", "amine's routine".

This command invokes Amine's beam optimization routine from hutch-python to
align the beam at the sample position. The routine already exists in the
hutch-python environment — this command drives it via the bridge.

---

> **TODO — requires input from Amine before this command can be completed:**
>
> - Exact function name and module in hutch-python
> - Required arguments (motor list, detector signal, convergence criteria)
> - Whether a CLI wrapper is needed or the function is directly callable
> - Typical runtime and any safety constraints
> - AMI signal to use as the optimization objective
>
> Assign to: **Claire** (coordinate with Amine)

---

## Phase 1: Check Prerequisites

Before running, verify via the bridge (read-only):

1. Bridge is connected
2. Beam is present (`are-we-ready` check or user confirmation)
3. Relevant motors are enabled and not in fault

---

## Phase 2: Confirm

This is a **beam-critical write operation** — device moves will be made.
Always confirm before executing.

```
Proposed action:
  Run beam alignment optimization routine
  Objective: {signal_name}
  Motors:    {motor_list}

This will move beam-steering devices. Shall I proceed?
```

---

## Phase 3: Execute

```python
# PLACEHOLDER — fill in with Amine's actual function call:
# from {module} import {function_name}
# {function_name}({args})
```

Use bridge timeout ≥ 120s — optimization routines can take time.

---

## Phase 4: Report

Report the final beam position and optimization metric value.
If the routine fails or diverges, restore original motor positions
and suggest manual alignment.
