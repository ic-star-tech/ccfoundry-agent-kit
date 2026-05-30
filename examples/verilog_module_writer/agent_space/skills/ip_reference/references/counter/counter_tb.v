`timescale 1ns / 1ps

module counter_tb;
    parameter WIDTH = 4;

    reg              clk, rst_n, enable, up_down, load;
    reg  [WIDTH-1:0] load_val;
    wire [WIDTH-1:0] count;
    wire             overflow, underflow;

    counter #(.WIDTH(WIDTH)) uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .up_down(up_down),
        .load(load), .load_val(load_val), .count(count),
        .overflow(overflow), .underflow(underflow)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer pass_count = 0;
    integer fail_count = 0;

    task check(input [WIDTH-1:0] exp_count, input exp_ovf, input exp_udf, input [127:0] label);
        begin
            if (count === exp_count && overflow === exp_ovf && underflow === exp_udf) begin
                $display("PASS: %0s  count=%0d ovf=%b udf=%b", label, count, overflow, underflow);
                pass_count = pass_count + 1;
            end else begin
                $display("FAIL: %0s  count=%0d(%0d) ovf=%b(%b) udf=%b(%b)", label,
                    count, exp_count, overflow, exp_ovf, underflow, exp_udf);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("counter_tb.vcd");
        $dumpvars(0, counter_tb);

        rst_n = 0; enable = 0; up_down = 1; load = 0; load_val = 0;
        #20; rst_n = 1;
        @(posedge clk); #1;
        check(0, 0, 0, "after_reset");

        // Count up 3 times
        enable = 1; up_down = 1;
        @(posedge clk); #1; check(1, 0, 0, "count_up_1");
        @(posedge clk); #1; check(2, 0, 0, "count_up_2");
        @(posedge clk); #1; check(3, 0, 0, "count_up_3");

        // Parallel load
        enable = 0; load = 1; load_val = 4'b1110;
        @(posedge clk); #1;
        load = 0;
        check(14, 0, 0, "parallel_load");

        // Count up to overflow
        enable = 1; up_down = 1;
        @(posedge clk); #1; check(15, 1, 0, "overflow_at_max");

        // Count down
        up_down = 0;
        @(posedge clk); #1; // wraps to 0 after overflow
        @(posedge clk); #1;
        // Keep counting down to 0
        enable = 0; load = 1; load_val = 4'b0001;
        @(posedge clk); #1; load = 0;
        enable = 1; up_down = 0;
        @(posedge clk); #1; check(0, 0, 1, "underflow_at_zero");

        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0) $display("ALL_TESTS_PASSED");
        else $display("SOME_TESTS_FAILED");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
