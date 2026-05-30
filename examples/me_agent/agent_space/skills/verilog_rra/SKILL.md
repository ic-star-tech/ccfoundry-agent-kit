---
name: verilog_rra
description: Build and verify Round Robin Arbiter testbenches using Icarus Verilog.
slash_command:
  cmd: /verilog_rra
  label: Verilog RRA
  desc: Write, compile, and simulate a Round Robin Arbiter testbench with iverilog.
---

# Verilog RRA Testbench Skill

## Trigger
Use this skill when the user asks to build, verify, or test a Round Robin
Arbiter (RRA) in Verilog, or when a Foundry requirement mentions RRA/verilog.

## Capabilities
- Write a parametric Round Robin Arbiter module (Verilog-2001)
- Generate a self-checking testbench with pass/fail assertions
- Compile using `iverilog` and simulate using `vvp`
- Parse simulation output to determine pass/fail

## Implementation Guide

### RRA Module (rra.v)
Use a mask-based approach for fair round-robin scheduling:
- Maintain a 4-bit mask register
- After granting bit[i], set mask to clear bits <= i
- Use lowest-set-bit extraction: `x & (~x + 1)`
- When masked_req is empty, wrap around to unmasked req

```verilog
// Round Robin Arbiter - 4 bit (Verilog-2001)
module rra (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  req,
    output reg  [3:0]  grant
);
    reg [3:0] mask;
    reg [3:0] masked_req;
    reg [3:0] sel;

    function [3:0] lsb;
        input [3:0] x;
        begin
            lsb = x & (~x + 4'b0001);
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mask  <= 4'b1111;
            grant <= 4'b0000;
        end else if (|req) begin
            masked_req = req & mask;
            if (|masked_req)
                sel = lsb(masked_req);
            else
                sel = lsb(req);
            grant <= sel;
            case (sel)
                4'b0001: mask <= 4'b1110;
                4'b0010: mask <= 4'b1100;
                4'b0100: mask <= 4'b1000;
                default: mask <= 4'b1111;
            endcase
        end else begin
            grant <= 4'b0000;
        end
    end
endmodule
```

### Testbench (rra_tb.v)
- Use `$dumpfile` / `$dumpvars` for waveform capture
- Include reset sequence (rst_n=0 for 20ns, then rst_n=1)
- Test single requests, round-robin all-request, and no-request cases
- Print PASS/FAIL for each test case
- Print summary with `ALL_TESTS_PASSED` or `SOME_TESTS_FAILED`

### Sandbox Commands
```bash
# Compile
iverilog -o rra_sim rra.v rra_tb.v

# Simulate
vvp rra_sim

# View waveforms (if GTKWave available)
gtkwave rra_tb.vcd
```

## Requirements
- Foundry sandbox with `iverilog` and `vvp` installed
- Workspace write access via sandbox API

## Success Criteria
- All testbench assertions pass
- `ALL_TESTS_PASSED` appears in simulation output
