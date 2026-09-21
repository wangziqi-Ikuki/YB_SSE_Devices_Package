# Legacy synthesis workflow exports

This directory preserves the three original JSON workflow exports supplied for the YB synthesis station. They are copied without changing their workflow UUIDs, node UUIDs, parameters, edges, or manual-confirmation steps.

| Package name | Original workflow | SHA-256 |
| --- | --- | --- |
| `synthesis_powder_bead_cabin.json` | 合成工站 加粉加珠流程 方舱 | `7f2eb6e822e53d48d98124aecb793a73dea4d0a354e1c1f330c95a2f0a8a70ba` |
| `synthesis_powder_bead_rack2.json` | 合成工站 加粉加珠流程 料架2 | `f8e7283ab6d8906f1d61c46514a0aebc593f794e654d78206774fe7bda32ae70` |
| `synthesis_mixing.json` | 合成工站混料流程 | `84db137a50d9b91e96cd444fd0237a2822708e4fd2ba21d329aec9f3ac8fc6ba` |

These files use the legacy exported-graph envelope (`name`, `target_lab_uuid`, and `data`). Current editable-package discovery accepts Python sources from `workflows/*.py` and `experiment_operations/*.py`; therefore these JSON assets are intentionally not listed in `package.yaml` and are not auto-registered directly.

Their current, auto-loaded Python counterparts are:

- `workflows/synthesis_powder_bead_cabin.py`
- `workflows/synthesis_powder_bead_rack2.py`
- `workflows/synthesis_mixing.py`

The conversions retain the original workflow UUIDs and action order. Legacy standalone manual-confirmation nodes are represented by the current OS manual-confirmation wrapper on the following action, with the same 3600-second timeout.

Use `load_legacy_workflow()` to read an export for migration or compatibility tooling. The automated tests validate graph integrity, action availability, package inclusion, and execution of the action sequence against the in-process synthesis-station simulator. Manual-confirmation nodes are preserved and validated as gates, while the automated simulator test treats approval as granted.
