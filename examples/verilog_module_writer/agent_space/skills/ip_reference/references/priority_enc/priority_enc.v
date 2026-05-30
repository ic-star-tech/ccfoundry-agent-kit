// ─── Priority Encoder ────────────────────────────────────────────
// Verilog-2001 | Parameterized width
// Outputs the index of the highest-priority (lowest-index) set bit.

module priority_enc #(
    parameter N = 8
) (
    input  wire [N-1:0]           req,
    output reg  [$clog2(N)-1:0]   idx,
    output reg                    valid
);
    integer i;

    always @(*) begin
        idx   = 0;
        valid = 1'b0;
        for (i = N-1; i >= 0; i = i - 1) begin
            if (req[i]) begin
                idx   = i[$clog2(N)-1:0];
                valid = 1'b1;
            end
        end
    end
endmodule
