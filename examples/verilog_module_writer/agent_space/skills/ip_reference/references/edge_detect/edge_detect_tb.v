`timescale 1ns / 1ps

module edge_detect_tb;
    reg  clk, rst_n, sig_in;
    wire rising, falling, any_edge;

    edge_detect uut (
        .clk(clk), .rst_n(rst_n), .sig_in(sig_in),
        .rising(rising), .falling(falling), .any_edge(any_edge)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer pass_count = 0;
    integer fail_count = 0;

    task check(input exp_r, input exp_f, input exp_a, input [127:0] label);
        begin
            if (rising === exp_r && falling === exp_f && any_edge === exp_a) begin
                $display("PASS: %0s  r=%b f=%b a=%b", label, rising, falling, any_edge);
                pass_count = pass_count + 1;
            end else begin
                $display("FAIL: %0s  r=%b(%b) f=%b(%b) a=%b(%b)", label,
                    rising, exp_r, falling, exp_f, any_edge, exp_a);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("edge_detect_tb.vcd");
        $dumpvars(0, edge_detect_tb);

        rst_n = 0; sig_in = 0;
        #20; rst_n = 1;

        // Steady low → no edges
        @(posedge clk); #1;
        check(0, 0, 0, "steady_low");

        // Rising edge
        sig_in = 1;
        @(posedge clk); #1;
        check(1, 0, 1, "rising_edge");

        // Steady high → no edges
        @(posedge clk); #1;
        check(0, 0, 0, "steady_high");

        // Falling edge
        sig_in = 0;
        @(posedge clk); #1;
        check(0, 1, 1, "falling_edge");

        // Back to steady low
        @(posedge clk); #1;
        check(0, 0, 0, "steady_low_again");

        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0) $display("ALL_TESTS_PASSED");
        else $display("SOME_TESTS_FAILED");
        $finish;
    end

    initial begin #10000; $display("TIMEOUT"); $finish; end
endmodule
