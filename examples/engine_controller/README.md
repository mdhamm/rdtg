# Example: ECU-7 engine controller

One example in two halves, kept apart the same way they would be on a real program.

| Folder | Plays the role of | Owned by |
|---|---|---|
| [`software/`](software/) | The software under test: `engine_controller.py` and its interface control document [`ICD.md`](software/ICD.md). It does not use RDTG. | The software team |
| [`workspace/`](workspace/) | The RDTG workspace that tests it: `requirements.yaml`, `catalog.yaml`, `rdtg.yaml`, and the rig adapters in `rig/engine_rig.py`. | The systems / test team |

They connect **only** through the interfaces described in the ICD:

```
workspace/requirements.yaml ──► generated tests ──► workspace/rig/engine_rig.py ──TCP/UDP──► software/engine_controller.py
```

The requirements and catalog use system-level names (`IGNITION_SW = ON`). The software uses its
own channel names and encodings (`DI_IGN = 1`). `rig/engine_rig.py` is the only place that knows
both.

## Try it

From the repository root:

```sh
rdtg serve examples/engine_controller/workspace             # or open it from the workspace page
rdtg generate examples/engine_controller/workspace          # writes workspace/out/
rdtg targets examples/engine_controller/workspace --check  # which targets are usable here
rdtg run examples/engine_controller/workspace                # default target: Simulated Hardware
rdtg run examples/engine_controller/workspace --param bugs=slow_spinup   # with a bug in the software
```

## Targets (`workspace/rdtg.yaml`)

| Target | Kind | Adapters | Runs here? |
|---|---|---|---|
| Simulated Hardware | simulated | ECU-7 launched on this PC; I/O box, 1553 and Ethernet emulated over localhost | yes |
| Partially Simulated Hardware | partial | emulated I/O box, **real 1553 card** (`rig/bench_drivers.py`) | no: needs the DDC driver |
| Hardware | hardware | **real I/O and 1553 cards** on the HIL bench | no: needs the bench drivers |

`rig/bench_drivers.py` contains templates for the real cards. Fill in the vendor SDK calls and
those targets become usable without changing the generated tests.
Bug switches for the `bugs` parameter: `fuel_low_boundary`, `no_fuel_pump_check`, `slow_spinup`,
`slow_mode`, `maint_ignored`.
