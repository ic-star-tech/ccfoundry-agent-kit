`timescale 1ns / 1ps

module rra_tb;
    reg        clk;
    reg        rst_n;
    reg  [3:0] req;
    wire [3:0] grant;

    rra uut (
        .clk   (clk),
        .rst_n (rst_n),
        .req   (req),
        .grant (grant)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer pass_count = 0;
    integer fail_count = 0;

    task check(input [3:0] expected, input [127:0] label);
        begin
            if (grant === expected) begin
                $display("PASS: %0s  req=%b grant=%b (expected %b)", label, req, grant, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("FAIL: %0s  req=%b grant=%b (expected %b)", label, req, grant, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("rra_tb.vcd");
        $dumpvars(0, rra_tb);

        // Reset
        rst_n = 0; req = 4'b0000;
        #20;
        rst_n = 1;
        @(posedge clk); #1;
        check(4'b0000, "after_reset_no_req");

        // T1: Single request bit 0
        req = 4'b0001; @(posedge clk); #1;
        check(4'b0001, "single_req_bit0");

        // T2: Single request bit 2
        req = 4'b0100; @(posedge clk); #1;
        check(4'b0100, "single_req_bit2");

        // T3-T6: All request - should round-robin
        req = 4'b1111; @(posedge clk); #1;
        check(4'b1000, "all_req_cycle1");
        @(posedge clk); #1;
        check(4'b0001, "all_req_cycle2");
        @(posedge clk); #1;
        check(4'b0010, "all_req_cycle3");
        @(posedge clk); #1;
        check(4'b0100, "all_req_cycle4");

        // T7: No request
        req = 4'b0000; @(posedge clk); #1;
        check(4'b0000, "no_req");

        // Summary
        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0)
            $display("ALL_TESTS_PASSED");
        else
            $display("SOME_TESTS_FAILED");
        $finish;
    end

    // Safety timeout
    initial begin #10000; $display("TIMEOUT"); $finish; end
endmodule
