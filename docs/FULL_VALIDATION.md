# Release validation

From the project directory, using the robot Python environment:

```bat
set CAD_MUJOCO_PANDA_SCENE=C:\path\to\mujoco_menagerie\franka_emika_panda\scene.xml
python full_validation.py --cad-python .venv-drawing2cad\Scripts\python.exe
```

`--python` selects the MuJoCo/NumPy/SciPy/Pillow/Matplotlib/jsonschema interpreter.
`--cad-python` selects the CadQuery/OpenCV/PyMuPDF/RapidOCR/ReportLab/pytest interpreter.
`--output` must be new or empty, preventing stale results from being accepted.
Panda paths can alternatively be supplied with `--panda-scene`.

The reviewed manifest in `validation_tools/manifest.py` requires all module suites,
the original ball tests, B1/B2/B3 acceptance scripts, infrastructure tests and E2E.
Collection AND executed counts must match. Failures, errors, skips, xfail, xpass,
duplicate IDs, crashed workers and missing environments fail the gate.
Unittest subTests remain assertions in one method; they do not inflate test counts.
Class setup/collection errors are additionally listed as `runner_errors`.

Each group saves its log, case verdicts, duration and a validation JSON. The root
`validation.json` separates framework cases, benchmark acceptance cases, setup
errors, native MuJoCo step counts (including fixture work), test gate status and
release gate status. A fully passing test suite can still have `success=false`
when independent drop diagnostics expose a release blocker. No skip is a pass.

The perception small/large fixtures test registration and grasp, not stable-drop
acceptance. Their converter's independent `cad_drop_success` remains recorded.
Failures are surfaced as release blockers, not discarded. Reproduce contact
diagnostics without changing production settings:

```bat
python scripts\diagnose_perception_drop.py --panda-scene "%CAD_MUJOCO_PANDA_SCENE%" --output outputs\drop_diagnostics
```

E2E generates a fresh engineering PDF, invokes the real CAD parser/exporter in the
CAD interpreter, then converts its new STL/JSON and runs the real Panda/RGB-D/Agent
skills. It requires successful drop validation before executing skills. Artifacts
include the input drawing, parsed JSON, STEP/STL/preview, scenes, drop report,
RGB/depth/segmentation, point clouds, task plan, transitions, trace, candidates,
separate offline evaluation and `e2e/artifacts/validation.json`. No existing part
STL/MJCF is used as the input. Panda robot assets are an external dependency.

## Outcome fields (reporting only; existing thresholds unchanged)

| Field | Meaning |
| --- | --- |
| `perception_success` | All attempted pose estimates succeeded and at least one exists. `null` for ground-truth-only manipulation. Does not certify GT error or task completion. |
| `grasp_execution_completed` | Approach/close/lift completed (including the pipeline's hold phase). Does not assert that an object is actually held. |
| `visual_task_success` | The existing task completed with fresh post-release visual verification. `null` for grasp-only pipelines. |
| `offline_ground_truth_metrics` | Post-execution simulation evaluation only. Never provided to the Agent or used to rescue a visual failure. `null` if unavailable. |

For compatibility, grasp-only `success` retains its offline physics acceptance
meaning; task `success` aliases `visual_task_success`. `success_semantics` makes
that legacy distinction explicit. Existing `pose_evaluation`/`offline_evaluation`
remain compatibility fields. SkillResult.success means that skill call completed
its contract; pick reports execution completion and verify reports visual success.
The Agent boundary deliberately excludes all offline ground-truth metrics.

This is one integration scenario, not coverage of arbitrary drawings/objects,
sensor noise, hardware transfer or unlimited stability. CAD slot inference and
convex collision approximations retain their existing limitations.
