// ─── Round Robin Arbiter (4-bit, mask-based) ─────────────────────
// Verilog-2001 | Async active-low reset
// After granting bit[i], mask clears bits <= i for fair rotation.
//
// Interface:
//   clk     - System clock
//   rst_n   - Async active-low reset
//   req     - 4-bit request (one-hot or multi-hot)
//   grant   - 4-bit grant (one-hot)

module rra (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  req,
    output reg  [3:0]  grant
);
    reg [3:0] mask;
    reg [3:0] masked_req, sel;

    // Lowest-set-bit extraction
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
