---
name: ip_reference
description: Browse the IP portfolio with reference implementations of common digital modules.
slash_command:
  cmd: /ip
  label: IP Reference
  desc: Browse the IP portfolio and reference implementations (RRA, FIFO, etc.).
---

# IP Reference Portfolio

## Trigger
Use this skill when implementing standard digital modules. Look up the
reference code from the `references/` subdirectory instead of writing from scratch.

## How to Use

1. Check the portfolio table below to find a matching module
2. Read the `.v` file from `references/<module>/` for the implementation
3. Read the `_tb.v` file for the testbench template
4. Adapt parameters as needed by the task spec
5. Compile and simulate in the Foundry sandbox

**File locations** (relative to this skill directory):
```
references/
├── rra/             # Round Robin Arbiter
│   ├── rra.v
│   └── rra_tb.v
├── sync_fifo/       # Synchronous FIFO
│   ├── sync_fifo.v
│   └── sync_fifo_tb.v
├── edge_detect/     # Edge Detector
│   ├── edge_detect.v
│   └── edge_detect_tb.v
├── priority_enc/    # Priority Encoder
│   ├── priority_enc.v
│   └── priority_enc_tb.v
├── pulse_sync/      # Pulse Synchronizer (CDC)
│   ├── pulse_sync.v
│   └── pulse_sync_tb.v
└── counter/         # Up/Down Counter
    ├── counter.v
    └── counter_tb.v
```

## IP Portfolio

| Module | Category | Params | Description | Verified |
|--------|----------|--------|-------------|----------|
| rra | Arbitration | N=4 | Mask-based Round Robin Arbiter, one-hot grant | ✅ iverilog |
| sync_fifo | Memory | DATA_W=8, DEPTH=4 | Synchronous FIFO with full/empty/count | ✅ iverilog |
| edge_detect | Utility | — | Rising/falling/both edge detector | ✅ iverilog |
| priority_enc | Logic | N=8 | Parameterized priority encoder with valid flag | ✅ iverilog |
| pulse_sync | CDC | — | Single-pulse clock domain crossing (3-stage sync) | ✅ iverilog |
| counter | Utility | WIDTH=8 | Up/down counter with load, overflow/underflow | ✅ iverilog |

## Design Conventions

All reference IPs follow the coding_style skill guidelines:
- Verilog-2001, ANSI port declarations
- Async active-low reset (`rst_n`)
- `always @(*)` for combinational, `always @(posedge clk or negedge rst_n)` for sequential
- Self-checking testbenches with PASS/FAIL and ALL_TESTS_PASSED summary
- `$dumpfile` / `$dumpvars` for waveform capture

## Sandbox Commands

```bash
# General pattern for any IP:
cd /workspace
iverilog -o <module>_sim <module>.v <module>_tb.v
vvp <module>_sim

# Example for RRA:
iverilog -o rra_sim rra.v rra_tb.v
vvp rra_sim
```
