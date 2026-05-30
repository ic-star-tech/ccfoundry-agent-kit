`timescale 1ns / 1ps

module priority_enc_tb;
    parameter N = 4;

    reg  [N-1:0]          req;
    wire [$clog2(N)-1:0]  idx;
    wire                  valid;

    priority_enc #(.N(N)) uut (.req(req), .idx(idx), .valid(valid));

    integer pass_count = 0;
    integer fail_count = 0;

    task check(input [$clog2(N)-1:0] exp_idx, input exp_valid, input [127:0] label);
        begin
            if (valid === exp_valid && (exp_valid == 0 || idx === exp_idx)) begin
                $display("PASS: %0s  req=%b idx=%0d valid=%b", label, req, idx, valid);
                pass_count = pass_count + 1;
            end else begin
                $display("FAIL: %0s  req=%b idx=%0d(%0d) valid=%b(%b)", label, req, idx, exp_idx, valid, exp_valid);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("priority_enc_tb.vcd");
        $dumpvars(0, priority_enc_tb);

        req = 4'b0000; #10;
        check(0, 0, "no_request");

        req = 4'b0001; #10;
        check(0, 1, "bit0_only");

        req = 4'b0010; #10;
        check(1, 1, "bit1_only");

        req = 4'b0110; #10;
        check(1, 1, "bit1_and_bit2");

        req = 4'b1000; #10;
        check(3, 1, "bit3_only");

        req = 4'b1111; #10;
        check(0, 1, "all_bits");

        $display("────────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass_count, fail_count);
        if (fail_count == 0) $display("ALL_TESTS_PASSED");
        else $display("SOME_TESTS_FAILED");
        $finish;
    end
endmodule
