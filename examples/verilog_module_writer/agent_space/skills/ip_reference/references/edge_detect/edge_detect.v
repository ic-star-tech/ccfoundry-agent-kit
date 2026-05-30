// ─── Edge Detector ───────────────────────────────────────────────
// Verilog-2001 | Rising, falling, and any-edge detection

module edge_detect (
    input  wire clk,
    input  wire rst_n,
    input  wire sig_in,
    output wire rising,
    output wire falling,
    output wire any_edge
);
    reg sig_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            sig_d <= 1'b0;
        else
            sig_d <= sig_in;
    end

    assign rising   = sig_in & ~sig_d;
    assign falling  = ~sig_in & sig_d;
    assign any_edge = sig_in ^ sig_d;
endmodule
