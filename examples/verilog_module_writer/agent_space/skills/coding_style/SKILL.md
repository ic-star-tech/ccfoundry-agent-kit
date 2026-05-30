---
name: coding_style
description: Verilog-2001 coding style guidelines and best practices for synthesizable RTL.
slash_command:
  cmd: /style
  label: Coding Style
  desc: Review Verilog coding style guidelines and best practices.
---

# Verilog Coding Style Guide

## Trigger
Use this skill when writing or reviewing Verilog code to ensure consistency,
readability, and synthesizability.

## General Rules

### File Organization
- One module per file
- Filename matches module name: `rra.v` contains `module rra`
- Testbench files use `_tb` suffix: `rra_tb.v`
- Use `.v` extension for Verilog-2001 (not `.sv`)

### Module Declaration (Verilog-2001 ANSI style)
```verilog
module module_name (
    input  wire        clk,
    input  wire        rst_n,     // active-low async reset
    input  wire [7:0]  data_in,
    output reg  [7:0]  data_out,
    output wire        valid
);
```

### Naming Conventions
| Element | Convention | Example |
|---------|-----------|---------|
| Modules | lowercase_snake | `round_robin_arbiter` |
| Signals | lowercase_snake | `data_valid`, `read_enable` |
| Parameters | UPPER_SNAKE | `DATA_WIDTH`, `ADDR_DEPTH` |
| Constants | UPPER_SNAKE | `IDLE`, `STATE_READ` |
| Active-low signals | `_n` suffix | `rst_n`, `cs_n`, `wr_n` |
| Clock signals | `clk` prefix | `clk`, `clk_div2` |
| Reset signals | `rst` prefix | `rst_n`, `rst_sync` |
| Registered outputs | `_reg` suffix (internal) | `count_reg`, `state_reg` |
| Next-state signals | `_next` suffix | `state_next`, `count_next` |

### Reset Strategy
- **Always use async active-low reset** (`negedge rst_n`)
- Reset all registers to known values
- Use consistent reset pattern:

```verilog
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state <= IDLE;
        count <= 0;
    end else begin
        state <= state_next;
        count <= count_next;
    end
end
```

### Combinational Logic
- Use `always @(*)` for combinational blocks
- Assign all outputs in every branch to avoid latches
- Use blocking assignments (`=`) in combinational blocks
- Use non-blocking assignments (`<=`) in sequential blocks

```verilog
// GOOD: Combinational with always @(*)
always @(*) begin
    grant = 4'b0000;  // default
    case (state)
        IDLE:    grant = 4'b0000;
        GRANT_0: grant = 4'b0001;
        GRANT_1: grant = 4'b0010;
        default: grant = 4'b0000;
    endcase
end
```

### State Machine Template
```verilog
// State encoding
localparam IDLE    = 2'b00;
localparam ACTIVE  = 2'b01;
localparam DONE    = 2'b10;

reg [1:0] state, state_next;

// State register (sequential)
always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
        state <= IDLE;
    else
        state <= state_next;
end

// Next-state logic (combinational)
always @(*) begin
    state_next = state;  // default: hold
    case (state)
        IDLE:    if (start) state_next = ACTIVE;
        ACTIVE:  if (done)  state_next = DONE;
        DONE:    state_next = IDLE;
        default: state_next = IDLE;
    endcase
end
```

### Parameterization
```verilog
module fifo #(
    parameter DATA_WIDTH = 8,
    parameter ADDR_DEPTH = 4
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire [DATA_WIDTH-1:0]   wr_data,
    output wire [DATA_WIDTH-1:0]   rd_data
);
    localparam FIFO_DEPTH = 1 << ADDR_DEPTH;
    // ...
endmodule
```

### Comments
- Module header: purpose, I/O description, author
- Section headers for logical blocks
- Explain non-obvious logic, not the obvious

```verilog
// ─── Round Robin Arbiter ─────────────────────────────────
// Mask-based RRA: after granting bit[i], mask clears bits ≤ i
// to ensure fair rotation among requestors.
```

### Testbench Style
- Use `timescale 1ns / 1ps`
- Clock generation: `initial clk = 0; always #5 clk = ~clk;`
- Self-checking with PASS/FAIL per test case
- Summary with total pass/fail counts
- Print `ALL_TESTS_PASSED` or `SOME_TESTS_FAILED`
- Use `$dumpfile` / `$dumpvars` for waveform capture
- Timeout safety: `initial begin #10000; $display("TIMEOUT"); $finish; end`

### Things to Avoid
- ❌ SystemVerilog features (`logic`, `always_ff`, `always_comb`)
- ❌ Variable declarations in unnamed blocks
- ❌ `initial` blocks in synthesizable code
- ❌ `#delay` in synthesizable code
- ❌ Incomplete sensitivity lists
- ❌ Mixing blocking/non-blocking in one always block
- ❌ Inferred latches (missing default in case/if)
