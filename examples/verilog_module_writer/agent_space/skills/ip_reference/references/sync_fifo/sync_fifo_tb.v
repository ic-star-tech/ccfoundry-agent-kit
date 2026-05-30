`timescale 1ns / 1ps

module sync_fifo_tb;
    parameter DATA_WIDTH = 8;
    parameter ADDR_DEPTH = 2;  // depth=4 for quick test
    localparam DEPTH = 1 << ADDR_DEPTH;

    reg                    clk, rst_n;
    reg                    wr_en, rd_en;
    reg  [DATA_WIDTH-1:0]  wr_data;
    wire [DATA_WIDTH-1:0]  rd_data;
    wire                   full, empty;
    wire [ADDR_DEPTH:0]    count;

    sync_fifo #(.DATA_WIDTH(DATA_WIDTH), .ADDR_DEPTH(ADDR_DEPTH)) uut (
        .clk(clk), .rst_n(rst_n),
        .wr_en(wr_en), .rd_en(rd_en),
        .wr_data(wr_data), .rd_data(rd_data),
        .full(full), .empty(empty), .count(count)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer pass_count = 0;
    integer fail_count = 0;

    task check_flags(input exp_full, input exp_empty, input [ADDR_DEPTH:0] exp_count, input [127:0] label);
        begin
            if (full === exp_full && empty === exp_empty && count === exp_count) begin
                $display("PASS: %0s  full=%b empty=%b count=%0d", label, full, empty, count);
                pass_count = pass_count + 1;
            end else begin
                $display("FAIL: %0s  full=%b(exp %b) empty=%b(exp %b) count=%0d(exp %0d)",
                    label, full, exp_full, empty, exp_empty, count, exp_count);
                fail_count = fail_count + 1;
            end
        end
    endtask

    integer i;
    initial begin
        $dumpfile("sync_fifo_tb.vcd");
        $dumpvars(0, sync_fifo_tb);

        rst_n = 0; wr_en = 0; rd_en = 0; wr_data = 0;
        #20; rst_n = 1;
        @(posedge clk); #1;
        check_flags(0, 1, 0, "after_reset");

        // Write until full
        for (i = 0; i < DEPTH; i = i + 1) begin
            wr_en = 1; wr_data = i + 8'hA0;
            @(posedge clk); #1;
        end
        wr_en = 0;
        check_flags(1, 0, DEPTH, "write_until_full");

        // Overflow protection: write when full
        wr_en = 1; wr_data = 8'hFF;
        @(posedge clk); #1;
        wr_en = 0;
        check_flags(1, 0, DEPTH, "overflow_protect");

        // Read all
        for (i = 0; i < DEPTH; i = i + 1) begin
            rd_en = 1;
            @(posedge clk); #1;
        end
        rd_en = 0;
        check_flags(0, 1, 0, "read_until_empty");

        // Underflow protection: read when empty
        rd_en = 1;
        @(posedge clk); #1;
        rd_en = 0;
        check_flags(0, 1, 0, "underflow_protect");

        // Simultaneous read+write
        wr_en = 1; wr_data = 8'h42;
        @(posedge clk); #1;
        wr_en = 0;
        // now count=1
        wr_en = 1; rd_en = 1; wr_data = 8'h43;
        @(posedge clk); #1;
        wr_en = 0; rd_en = 0;
        check_flags(0, 0, 1, "simultaneous_rw");

        // Summary
        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0)
            $display("ALL_TESTS_PASSED");
        else
            $display("SOME_TESTS_FAILED");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
