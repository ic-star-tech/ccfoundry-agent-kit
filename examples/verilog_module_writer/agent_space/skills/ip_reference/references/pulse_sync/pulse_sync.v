// ─── Pulse Synchronizer (CDC) ────────────────────────────────────
// Verilog-2001 | Toggle-based single-pulse CDC
// Transfers a single-cycle pulse from clk_src to clk_dst domain.
// Uses 3-stage synchronizer to mitigate metastability.

module pulse_sync (
    input  wire clk_src,
    input  wire clk_dst,
    input  wire rst_n,
    input  wire pulse_in,     // single-cycle pulse in clk_src domain
    output wire pulse_out     // single-cycle pulse in clk_dst domain
);
    reg toggle_src;
    reg [2:0] sync_dst;       // 3-stage synchronizer

    // Source domain: toggle on pulse
    always @(posedge clk_src or negedge rst_n) begin
        if (!rst_n)
            toggle_src <= 1'b0;
        else if (pulse_in)
            toggle_src <= ~toggle_src;
    end

    // Destination domain: synchronize + detect edge
    always @(posedge clk_dst or negedge rst_n) begin
        if (!rst_n)
            sync_dst <= 3'b000;
        else
            sync_dst <= {sync_dst[1:0], toggle_src};
    end

    assign pulse_out = sync_dst[2] ^ sync_dst[1];
endmodule
