// ─── Synchronous FIFO ────────────────────────────────────────────
// Verilog-2001 | Parameterized depth & width
// Single-clock FIFO with full, empty, and count outputs.
//
// Parameters:
//   DATA_WIDTH - Data bus width (default 8)
//   ADDR_DEPTH - Address bits (depth = 2^ADDR_DEPTH, default 4 → 16 entries)

module sync_fifo #(
    parameter DATA_WIDTH = 8,
    parameter ADDR_DEPTH = 4
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    wr_en,
    input  wire                    rd_en,
    input  wire [DATA_WIDTH-1:0]   wr_data,
    output wire [DATA_WIDTH-1:0]   rd_data,
    output wire                    full,
    output wire                    empty,
    output reg  [ADDR_DEPTH:0]     count
);
    localparam DEPTH = 1 << ADDR_DEPTH;

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    reg [ADDR_DEPTH-1:0] wr_ptr, rd_ptr;

    assign full  = (count == DEPTH);
    assign empty = (count == 0);
    assign rd_data = mem[rd_ptr];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= 0;
            rd_ptr <= 0;
            count  <= 0;
        end else begin
            case ({wr_en & ~full, rd_en & ~empty})
                2'b10: begin  // write only
                    mem[wr_ptr] <= wr_data;
                    wr_ptr <= wr_ptr + 1;
                    count  <= count + 1;
                end
                2'b01: begin  // read only
                    rd_ptr <= rd_ptr + 1;
                    count  <= count - 1;
                end
                2'b11: begin  // simultaneous read+write
                    mem[wr_ptr] <= wr_data;
                    wr_ptr <= wr_ptr + 1;
                    rd_ptr <= rd_ptr + 1;
                    // count unchanged
                end
                default: ; // no-op
            endcase
        end
    end
endmodule
