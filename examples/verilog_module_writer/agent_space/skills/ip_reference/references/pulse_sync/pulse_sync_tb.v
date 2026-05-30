`timescale 1ns / 1ps

module pulse_sync_tb;
    reg  clk_src, clk_dst, rst_n, pulse_in;
    wire pulse_out;

    pulse_sync uut (
        .clk_src(clk_src), .clk_dst(clk_dst),
        .rst_n(rst_n), .pulse_in(pulse_in), .pulse_out(pulse_out)
    );

    // Source clock: 100 MHz (10ns period)
    initial clk_src = 0;
    always #5 clk_src = ~clk_src;

    // Destination clock: 66 MHz (15ns period) — different frequency
    initial clk_dst = 0;
    always #7.5 clk_dst = ~clk_dst;

    integer pass_count = 0;
    integer fail_count = 0;
    integer pulse_detected;

    task wait_for_pulse_out(input integer max_cycles);
        integer cyc;
        begin
            pulse_detected = 0;
            for (cyc = 0; cyc < max_cycles; cyc = cyc + 1) begin
                @(posedge clk_dst); #1;
                if (pulse_out) begin
                    pulse_detected = 1;
                    cyc = max_cycles; // break
                end
            end
        end
    endtask

    initial begin
        $dumpfile("pulse_sync_tb.vcd");
        $dumpvars(0, pulse_sync_tb);

        rst_n = 0; pulse_in = 0;
        #40; rst_n = 1;
        #20;

        // T1: Send a single pulse in source domain
        @(posedge clk_src);
        pulse_in = 1;
        @(posedge clk_src);
        pulse_in = 0;

        // Wait for it to appear in dst domain (allow sync latency)
        wait_for_pulse_out(10);
        if (pulse_detected) begin
            $display("PASS: pulse_transfer_1");
            pass_count = pass_count + 1;
        end else begin
            $display("FAIL: pulse_transfer_1 (not detected in 10 dst cycles)");
            fail_count = fail_count + 1;
        end

        #100;

        // T2: Second pulse
        @(posedge clk_src);
        pulse_in = 1;
        @(posedge clk_src);
        pulse_in = 0;

        wait_for_pulse_out(10);
        if (pulse_detected) begin
            $display("PASS: pulse_transfer_2");
            pass_count = pass_count + 1;
        end else begin
            $display("FAIL: pulse_transfer_2");
            fail_count = fail_count + 1;
        end

        #100;

        // T3: No pulse → no output
        // Wait and verify pulse_out stays low
        pulse_detected = 0;
        repeat(5) begin
            @(posedge clk_dst); #1;
            if (pulse_out) pulse_detected = 1;
        end
        if (!pulse_detected) begin
            $display("PASS: no_spurious_pulse");
            pass_count = pass_count + 1;
        end else begin
            $display("FAIL: no_spurious_pulse");
            fail_count = fail_count + 1;
        end

        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0) $display("ALL_TESTS_PASSED");
        else $display("SOME_TESTS_FAILED");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
