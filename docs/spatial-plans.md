# Microphone positions, seats and resumable capture

Eight or more measured positions can participate in one offline correction.
The point count does not create more receiver DSP channels: the result still
uses the existing per-output filter budgets and supported native controls.

The measurement model distinguishes:

| Item | Meaning |
|---|---|
| Position | A declared physical microphone location with a stable ID |
| Seat / area | An explicitly assigned group of positions with a total priority |
| Repeat | Another response from the same output at the same location |
| Capture run | One explicitly started acquisition attempt, possibly partial |
| Validation point | A measured location excluded from fitting and step selection |

Creating an eight-point plan around one recorded point leaves **one measured
location**. It never expands two repeated scans into two spatial locations.
Historical TV-guided Audyssey calibration does not establish that its original
individual responses have been imported; the program has no verified historical
eight-point retrieval path. Import a saved raw-response file or acquire new data.

## Plan and inspect coverage

Open **Measurements → Microphone positions and measurement plan**. Create a
three-seat, single-area or observed-ID draft. Review the seat assignment,
within-seat priority, repeat target, placement note and training/validation role
for every point. Imported arbitrary position IDs can be retained explicitly.
There is no inference of physical location from an unverified label.

Draft checks read local records only. **Save measurement plan** binds the intended
output matrix to the project and invalidates older designs/transfer reviews.
Changing centre/side priorities preserves all points in an existing custom
three-seat plan; it does not replace them with the legacy seven-point template.

Coverage is evaluated for every planned **position × expected wired output ×
target repeat** cell. Extra repeats at one position cannot fill another missing
position or speaker. Unverified legacy subwoofer addresses remain flagged and
do not satisfy valid capture coverage. A complete matrix is not evidence that
microphone compensation, absolute timing or playback reconstruction is verified.

Plans allow 1–256 points and 1–64 seat/area IDs within the existing 1,024-response
and archive memory budgets. Native acquisition retains the 1–20 position-number
control; the separate stable position ID can name an imported/custom location.
Each batch uses a one-position acquisition session; a resumed point may have
several sessions, recorded separately without asserting common timing. These are software planning
limits, not a claim about the receiver's built-in calibration slot count.

## Acquire one physical location at a time

**Prepare next missing position** fills the acquisition controls and leaves
microphone readiness unconfirmed. It does not connect, enter calibration mode,
play a tone or automatically advance to another location.

Choose between:

- **Fill missing responses**: acquire only the deficit up to the selected repeat
  target, grouping outputs by their remaining repeat count.
- **Add requested repeats**: perform the requested number of additional scans
  on each selected output while preserving existing responses.

**Preview capture scope** returns the exact local batch list and a dataset-bound
scope fingerprint. Starting capture checks that fingerprint before refreshing
the receiver. A completed point or stale preview stops without requesting tones.
Normal capture still requires a connected receiver, explicit microphone-ready
confirmation and the observed Float protocol. No unattended microphone moves
or multi-position playback sequence is introduced.

Every run has a unique ID and receipt. Original records carry that run ID without
sample shifts or gain normalization. Per-run `.awb` checkpoints are saved under
`captures/`; a new attempt does not overwrite another run's checkpoint. Partial
responses are merged into a new project snapshot. If final project saving fails,
the previous project remains available and the per-run checkpoint is retained.
After interruption, preview the same position in resume mode to measure its
remaining outputs/repeats. The complete original AVR EQ is not backed up by
these measurement artifacts.

## Fit and validate

Saved validation roles are always excluded from fitting, including native
control search and EQ refinement. Additional explicitly selected holdouts can
also be used. Seat priorities are distributed among training points within each
seat; repeat counts do not multiply a seat's total priority. A validation-only
seat participates in evaluation, not in the training-priority normalization.

An incomplete explicit plan blocks normal design. An explicitly provisional
offline design can be requested for inspection, but its pending training or
validation/output/repeat cells prevent preparation as final receiver calibration.
Changing the plan also invalidates saved recipe matching through the dataset
fingerprint. Existing projects with no explicit spatial plan retain their old
fingerprints and legacy behavior.

## Local APIs

| Endpoint | Purpose |
|---|---|
| `POST /api/spatial-plan/preview` | Validate a draft/preset and inspect actual coverage; no mutation or hardware access |
| `POST /api/spatial-plan` | Save an explicit plan and invalidate prior designs |
| `POST /api/native-measure/preview` | Enumerate the required batches from retained responses only |
| `POST /api/native-measure/start` | Acquire one confirmed physical point; accepts the reviewed `preview_sha256` |

All POST requests require the existing local session token. A supplied `plan`
uses schema `audioseyx-spatial-plan-1` with `expected_channels`, `seats` and
`positions`. Presets accept `preset` (`three_seat`, `single_area`, `observed`),
`count`, `centre_priority` and `repeats`. No API reconstructs missing IRs.
