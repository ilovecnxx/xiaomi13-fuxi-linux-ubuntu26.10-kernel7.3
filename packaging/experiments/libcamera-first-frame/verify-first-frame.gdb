set pagination off
set confirm off
set breakpoint pending on
set debuginfod enabled off
set environment LIBCAMERA_SOFTISP_MODE cpu
set environment MALLOC_PERTURB_ 191
set $first_frame_seen = 0
break libcamera::DebayerCpu::process
commands
silent
if frame == 0
set $first_frame_seen = 1
print params
python
import gdb
import math

params = gdb.parse_and_eval("params")
expected = {"blackLevel": [0.0] * 3, "gains": [1.0] * 3,
            "combinedMatrix": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]}
for name, values in expected.items():
    actual = params[name]["data_"]["_M_elems"]
    if any(float(actual[i]) != value for i, value in enumerate(values)):
        gdb.write("FAIL: invalid first-frame " + name + "\n")
        gdb.execute("quit 1")
if not math.isclose(float(params["gamma"]), 1.0 / 2.2, rel_tol=1e-6):
    gdb.write("FAIL: invalid first-frame gamma\n")
    gdb.execute("quit 1")
if float(params["contrastExp"]) != 1.0:
    gdb.write("FAIL: invalid first-frame contrast\n")
    gdb.execute("quit 1")
gdb.write("PASS: initialized first-frame parameters under MALLOC_PERTURB_=191\n")
end
disable 1
end
continue
end
run
if $_isvoid($_exitcode)
thread apply all bt 8
quit 1
end
if $_exitcode != 0 || $first_frame_seen != 1
quit 1
end
quit 0
