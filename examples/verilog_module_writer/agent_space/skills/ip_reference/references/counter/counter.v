// ─── Up/Down Counter ─────────────────────────────────────────────
// Verilog-2001 | Parameterized width
// Features: up/down, parallel load, overflow/underflow flags

module counter #(
    parameter WIDTH = 8
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire             enable,
    input  wire             up_down,    // 1=up, 0=down
    input  wire             load,
    input  wire [WIDTH-1:0] load_val,
    output reg  [WIDTH-1:0] count,
    output wire             overflow,
    output wire             underflow
);
    assign overflow  = enable & up_down  & (count == {WIDTH{1'b1}});
    assign underflow = enable & ~up_down & (count == {WIDTH{1'b0}});

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= {WIDTH{1'b0}};
        else if (load)
            count <= load_val;
        else if (enable)
            count <= up_down ? count + 1 : count - 1;
    end
endmodule
