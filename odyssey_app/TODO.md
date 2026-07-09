---
name: Odyssey app TODO list
description: Open TODOs for the Odyssey scanner app — intensity/quantification is top priority, plus UX and export improvements
type: project
originSessionId: 2b182133-b93c-4031-a707-b8fcf5b67f37
---
## Branch plan (2026-07-09)

Remaining open items grouped into feature branches. Each branch is
scoped to touch a coherent slice of code, be reviewable in one pass,
and land as a single deploy candidate. Order = suggested order of
implementation (top = ship first).

| Branch | Contains | Test surface | State |
|---|---|---|---|
| `feature-form-clarity` | #6 (required→optional), #10 (HAP ID), #14 (initials only), #20 (invert=black-on-white), #21 (placeholders as examples) | Frontend + light backend; no scan needed to verify | pending |
| `feature-metadata-flow` | #11 (pre-scan minimum + during-scan editable), #17 (reopen prior project) | Frontend workflow; needs a saved record to verify | pending |
| `feature-display-and-lanes` | #2 (percentile contrast stretch), #18 (numbered lane strip + sample table on export) | Real scan needed for stretch; export-side for lanes | pending |
| `feature-connection-heartbeat` | #8 (heartbeat / stale-tab), #4 (cooling detection) | Backend heartbeat + frontend banner; hardware to trigger cooling | pending |
| `feature-pdf-report` | #12 (PDF report replacing JSON) | Backend renderer; drops into experiment ZIP | pending |
| `feature-lab-file-routing` | #5 (C:\data\employees\students\year\operator routing + auto-export) | Windows-side path config; needs lab-box test | pending |
| `feature-provenance-qr` | #16 (QR/barcode in image corner), #3c-e (intensity hardware equivalence protocol) | Real scan needed for equivalence test | pending |
| deferred | #13 (click-to-label lanes — "for later, lot of work") | — | deferred |

## Shipped (`lab-2026-05-01` + 2026-07-09 feature branch)
- ✅ #1 raw 16-bit TIFF export (Normal TIFF + ImageJ HyperStack)
- ✅ #7 real PLR integration (Phase 33+)
- ✅ #9 red/green tint restored in presentation export
- ✅ #15 PIDinst Handle in TIFF metadata (extended schema in private tag 65000)
- ✅ #19 transform record stamped in every presentation export

## Top priority — data correctness

1. **✅ SHIPPED 2026-07-09** — TIFF export now preserves raw 16-bit data. Three-button design implemented on branch `feature-raw-tiff-exports`:
   - **Normal TIFF** → `/api/image/raw-both` writes two raw 16-bit grayscale TIFFs (`<scan>-700-raw.tif`, `<scan>-800-raw.tif`) to the exports folder. Provenance JSON in TIFF private tag 65000 via `tag_tiff_with_identity` (rewritten on top of `tifffile`, preserves vendor tags 270/305 verbatim).
   - **ImageJ TIFF** → `/api/image/hyperstack` writes a single ImageJ HyperStack (C=2, 16-bit each, red/green LUT metadata). Opens colored in Fiji, quantifiable per channel.
   - **Presentation TIFF** → existing `/api/image/export` path, rebadged; now with the transform record and the red/green tint bug fixed.
   - Metadata schema: identity + scan_params + session + transforms. `transforms=null` on raw paths; populated on presentation.

   Original problem statement preserved below for reference:

   > TIFF export must preserve raw 16-bit data (refined 2026-05-11, reinforced 2026-07-09 with second lab-user screenshot) — current TIFF export goes through the display canvas and ends up as 8-bit RGB with quantization splotches in the background. **Evidence (lab user, 2026-07-09)**: side-by-side ImageJ screenshots of `Nr3c1-800.tif` (raw scanner, 531×472, **16-bit grayscale**, 490 KB, clean background) vs `Nr3c1__overlay-tiff.tif` (our export, 531×552 including the 80-row metadata footer, **RGB 8-bit**, 1.1 MB, visibly blotchy background at the same sharpness). Blotchiness = 8-bit dithering in low-signal areas; band-region pixels still read sharply because they're above the quantization noise floor. Densitometry on this would inflate background estimates and corrupt ratio quantification.

   ### 1a. Raw per-channel TIFF (primary quantification artefact)
   - Route: `/api/image/tiff/{channel}` at `odyssey_app/app.py:850` → `download_channel` at `plr_v4/odyssey/image_backend.py:39` → `download_tiff` at `plr_v4/odyssey/connection.py:601` → vendor endpoint `GET /scan/image/<scan>-<ch>.tif?xml=<format>tiff</format>...`.
   - Returns the 16-bit grayscale TIFF from the scanner. Tagged via `_tag_tiff` → `tag_tiff_with_identity` (`plr_v4/odyssey/tagging.py:59`).

   #### Current metadata stamped
   - **Tag 270 (ImageDescription)** — JSON with DeviceCard identity (PIDinst Handle URI, landing page, human-readable instrument name) + `scan_name` + `channel`.
   - **Tag 305 (Software)** — `"Odyssey Western Blot Imager (PLR_v4)"`.

   #### Missing from current stamp (add before shipping raw as a quantification-of-record artefact)
   Tag 270 JSON needs to grow to include the scan params + provenance:
   - **Scan params from `ScanParameters` (`connection.py:87`)**: `intensity_700`, `intensity_800`, `focus`, `resolution`, `quality`, `origin_x`, `origin_y`, `width`, `height`, `channels`.
   - **Session context**: `operator`, `experiment_id`, `scan_completed_at`, `scan_name` (already there).
   - **Empty transform block**: `{"transforms": null}` — signals to tools that this file is raw, quantify directly.
   - Same schema used in presentation exports (#19) so raw ↔ presentation share a metadata contract, only the transform block differs (`null` vs populated).

   #### Preserve vendor tags when stamping (user, 2026-07-09)
   Whenever we stamp OUR metadata into a vendor TIFF, we must preserve the vendor's original tags too. LI-COR's scanner presumably writes provenance-critical fields — instrument serial, firmware version, scanner-side scan date, calibration state, possibly private tags with scan parameters. Silently dropping them destroys the audit trail from the instrument side even if we add our own on the app side.

   **Recommended strategy — "read first, then restamp"**:
   1. Read the vendor TIFF into memory with a library that preserves ALL tags (standard + private).
   2. Enumerate every tag present. Log/inspect once to understand what LI-COR writes.
   3. Add OUR tags without overwriting vendor's.
   4. Write back with all tags intact.

   **Recommended library**: switch `tag_tiff_with_identity` (`plr_v4/odyssey/tagging.py:59`) from `PIL.Image.save` to **`tifffile`** — bio-imaging standard, round-trips arbitrary TIFF tags cleanly, supports OME-XML and ImageJ metadata natively. Also unlocks the HyperStack path (#1b) so both features land in one dependency addition. `PIL.Image.save(out, format="TIFF")` at `tagging.py:93` currently re-encodes and may silently drop non-standard vendor tags — even though 16-bit pixel data is preserved (`mode="I;16"` round-trips), byte-level the file isn't identical to what the vendor emits.

   **Tag collision handling** (what if vendor already wrote tag 270 or 305):
   - **Tag 270 (ImageDescription)** — most likely used by vendor. Don't overwrite. Options: (a) preserve vendor's string, put OUR JSON in a private tag (65000–65535 range, reserved for private use); (b) merge into a structured JSON blob in 270: `{"vendor_description": "...", "wur": {...}}`. **Preferred**: (a) — cleanest, keeps vendor tag byte-identical.
   - **Tag 305 (Software)** — likely used by vendor ("LI-COR ..."). Same choice; prefer private tag for ours, leave 305 untouched.
   - **Tag 306 (DateTime), 315 (Artist), 33432 (Copyright)** — if vendor wrote these, preserve verbatim.

   **First step before deciding schema**: run a diagnostic — download one raw vendor TIFF, dump every tag it contains. `tifffile.TiffFile(path).pages[0].tags` gives the full list. Only then finalize which private tag numbers to use for our JSON schema. Diagnostic can be a one-off script; results captured in this TODO or a short `tagging_notes.md`.

   **Bit-preservation angle**: also solved by moving to `tifffile` — it preserves 16-bit `mode="I;16"` losslessly, same as PIL, plus keeps arbitrary vendor tags PIL drops. Pixel data was never the risk; provenance metadata is.

   #### UI wiring (decision 2026-07-09)
   - **Button label**: "Normal TIFF" — writes raw per-channel TIFFs (both 700 and 800) to `odyssey_app/exports/<experiment_id>/`.
   - **File naming**: `<scan>-700-raw.tif`, `<scan>-800-raw.tif`. `-raw` suffix so ImageJ users see which file is untouched.
   - **Not a browser download** — save to exports folder, same pattern as `/api/image/export` (return JSON confirmation, bundle in experiment ZIP). Current endpoint returns as download attachment; change to save-and-attach.

   ### 1b. ImageJ HyperStack TIFF (colored, ImageJ/Fiji-native)
   **Decision (2026-07-09): three export buttons, three distinct artefacts.**

   - **Button label**: "ImageJ TIFF"
   - **What it produces**: single ImageJ HyperStack TIFF with C=2 channels + LUT metadata → opens in Fiji already colored red/green, underlying channels remain 16-bit grayscale (quantifiable).
   - **File naming**: `<scan>-hyperstack.tif`, saved to `odyssey_app/exports/<experiment_id>/`.
   - **Implementation**: `tifffile.imwrite(path, np.stack([ch700, ch800]), photometric='minisblack', imagej=True, metadata={'axes':'CYX', 'LUTs':[(255,0,0),(0,255,0)]})`. Add `tifffile` to `requirements.txt` if not present.
   - **Metadata**: same schema as raw (#1a), stamped via `tifffile`'s `metadata=` and `description=` kwargs. Transform block = `null` (still raw pixels, only display LUTs added).
   - **What Windows-without-ImageJ shows**: first channel only, 16-bit grayscale. Windows Photos + Explorer thumbnails + Photoshop + PowerPoint "Insert Picture" all show grayscale, no red/green — ImageJ LUT metadata is not parsed by non-bio-imaging viewers. **Not a bug — it's the trilemma of "16-bit + red-green-on-Windows + standard-portable, pick 2 of 3."**
   - **Why ship it if Windows can't render it colored?** Two reasons: (i) users with Fiji get one-click viewing of the overlay without the manual `Image → Color → Merge Channels` step; (ii) it's a single self-contained file — nice for sharing/archiving vs two separate files.

   ### 1c. Presentation exports (PNG / 8-bit RGB TIFF) — the third button
   The "Windows-friendly, colored, drops into PowerPoint" export. Current `/api/image/export` path — 8-bit RGB, view-driven, tint applied, metadata footer drawn.
   - Keep the endpoint; rename the button to distinguish from the raw path.
   - **Button label**: "Presentation TIFF" (and "Presentation PNG" for the sibling PNG button)
   - **File naming**: `<scan>__<variant>.tiff` / `<scan>__<variant>.png` (variant = 700/800/overlay/etc; already implemented).
   - **Bugs blocking**: #9 (missing red/green tint on backend).
   - **Metadata**: transform record from #19 goes into TIFF tag 270 / PNG tEXt chunks.
   - **Honest labelling** requires: (i) raw is also exported so users can go back to source pixels, (ii) the transform record makes clear this file is derived, brightness=X, contrast=Y, stretch=off, etc.

   ### 1d. Format decision summary — three buttons, three files, three use cases
   | Button | Format | Bit depth | Color | Fiji | Windows Photos | Use case |
   |---|---|---|---|---|---|---|
   | **Normal TIFF** (1a) | Two grayscale TIFFs | 16-bit | none (grayscale) | one-click merge | grayscale ✓ | densitometry, publication data |
   | **ImageJ TIFF** (1b) | Single HyperStack | 16-bit × 2 channels | red/green in Fiji | opens colored | grayscale ch1 | Fiji users, single-file archive |
   | **Presentation** (1c) | PNG or 8-bit RGB TIFF | 8-bit | red/green baked | grayscale | red/green ✓ | slides, PowerPoint, sharing |

   ### Rules across all three
   - **LUT is display-time, not baked into pixels** (raw + HyperStack). Presentation *does* bake color — honest because raw is also exported.
   - **What NOT to do**: 16-bit RGB TIFF with (v,0,0)/(0,v,0) baked in. Looks right but forces analysts to un-tint before quantifying.
   - **Same metadata schema on all three files** (identity, scan params, transform record — differs only in whether transforms are populated).

   ### 1c. Presentation exports (PNG / 8-bit TIFF)
   - Keep the current `/api/image/export` path for slide/paper figures.
   - Rename UI button "TIFF + metadata" → "Presentation TIFF (view)" — makes the raw-vs-presentation distinction visible.
   - PNG stays "Presentation PNG (view)".
   - Bug: current export drops the red/green tint (see #9).

   ### Principle
   User, 2026-07-09: **capture pixels as-is, record every transform**. Anything ever labelled "TIFF" that has been through the 8-bit display canvas needs a different word ("presentation TIFF"). Any brightness/contrast/crop/invert/tint applied for display goes into the machine-readable transform record (#19) — never baked destructively into a file labelled raw.

2. **Display contrast stretching** (separate from export) — web app display is dimmer than vendor native software because we likely do naive 16→8 conversion for display. Implement percentile contrast stretch (clip 0.5% / 99.5%) for display, matching ImageJ/Odyssey vendor behavior. Display-only — does not affect exported data.

3. **Intensity setting: validation + persistence** (raised 2026-05-11, refined 2026-07-09) — quantification requires the intensity setting be (a) correctly transmitted to the scanner and (b) recorded with the scan. User (2026-07-09): *"the 'feeling' that in the original software the intensity 5 is different than our intensity 5 now"* — needs to be tested end-to-end.

   ### 3a. Mechanical equivalence (browser-form API — already confirmed as byte-identical)
   - `plr_v4/odyssey/connection.py:117-118` — `intensity_700: str = "5"` sent as form field `intensity700=5` in `to_form_data()` (`connection.py:145`) POSTed to `configure.pl`.
   - Docstring (`connection.py:90-91`) confirms field names + string encoding + low-intensity notation (`L2, L1.5, L1, L0.5`) captured verbatim from HAR files of the vendor's own browser interface.
   - **Conclusion**: over the browser-form API path, our `intensity700=5` is byte-identical to what the vendor's browser UI sends. Mechanically equivalent.

   ### 3b. Where "different" could actually come from
   - **Image Studio Classic (vendor desktop app)** — this is the app the lab user's original workflow probably used. It does **not** use the browser form; it talks to the instrument via LI-COR's proprietary desktop protocol. That path might map "intensity 5" to a different laser power setting than the browser-form does. If comparing our app vs Image Studio Classic, this is the likely gap.
   - **Warm-up state** — our app might skip a laser stabilization step Image Studio Classic runs before scanning. `initializing.pl` polling handles part of this; unclear if fully matches vendor sequence.
   - **Time-of-day drift** — laser power drifts over time; the same setting on different days gives different pixel counts. Standard for any laser scanner. Confounder for the test protocol below.
   - **Focus / origin drift** — if focus offset or origin differ between runs, pixel counts differ even at same laser intensity. Confirm both are held constant across the compared scans.

   ### 3c. Test protocol for hardware-equivalence
   Same gel, back-to-back on the same day (< 30 min between scans), at least three replicates each:
   1. Scan A: our app, intensity 5, focus 0.0, origin (0,0), 169 µm, single channel (say 800 nm).
   2. Scan B: vendor UI (either the browser form OR Image Studio Classic — test both if possible), intensity 5, same focus / origin / resolution / channel.
   3. Download raw 16-bit TIFF from each.
   4. In Fiji: measure mean pixel value in the same 100×100 region on both TIFFs (a strong band + a background area).
   5. **Pass**: means agree within ±2% (below shot-noise floor for a single scan).
   6. **Fail**: hunt discrepancy. Order to check: (a) form field capture on our POST (browser dev-tools comparison), (b) does Image Studio Classic use a different intensity table entirely, (c) warm-up sequence timing.

   ### 3d. Persistence (do first — it unblocks 3c)
   Intensity setting (and channel, scan name, operator, date, focus, resolution, origin) must be stored in:
   - JSON sidecar next to raw TIFF (already partially done via `WesternBlotRecord`)
   - **TIFF tag 270 (ImageDescription)** — visible via ImageJ → Image → Show Info. Needs the transform-record schema from #19.
   - PDF report footer (#12).
   - Bake this in **now** so hardware-equivalence testing has a machine-readable audit trail per scan.

   ### 3e. Quantification validation
   Colleague's quantification (ImageJ Gels tool or similar) on our raw TIFFs vs vendor-quantified results on same gel. Pass = quantification pipeline downstream of raw TIFF unchanged. This is the acceptance test for the app being "trustworthy for a publication."

## Open TODOs

4. **Configure fails when instrument is cold** — display shows "cooling", typically when instrument idle that day. Detect state, block Configure with visible status until ready, clear error if cooling persists.

5. **Export path = C:\data with employee/student/year/operator structure** —
   - Root: `C:\data\` splits into `employees\` and `students\`
   - Each user picks their folder once (persisted)
   - Organized by year underneath
   - Typing operator initials/name (e.g. "VB") auto-routes to the right folder
   - Auto-export on scan completion (no manual export step)

6. **Required fields → optional (per user preference)** — sample/antibody workflow shouldn't be forced on everyone. Users decide whether that metadata is required.

7. **Real PyLabRobot integration** — replace current shim/driver with the actual PLR Odyssey backend.

8. **Stale browser tab when service isn't running** — first user (2026-05-11 era) opened an already-open tab, filled form, hit Configure, hung silently. Users assume "app open = app running." Need:
   - Heartbeat ping to backend on page load + periodic; failure → blocking banner with start instructions
   - Always-visible connection indicator (green/red) in header
   - Configure (and other actions) fail fast with clear error toast, not hang

9. **✅ SHIPPED 2026-07-09** — Red/green tint restored in presentation exports. `_render_export_image` (app.py) now tints 700→red and 800→green via a multiply blend that mirrors `drawChannelLayer` in index.html, before `ImageChops.lighter` composites the overlay. Skipped when `bw=true`. Original bug:

   **PNG export = export current view** (user feedback 2026-05-11, bug confirmed 2026-07-09) — PNG (presentation) export should mirror what's on screen:
   - If cropped → export crop only ✓ (frontend sends `crop`)
   - If color overlay displayed → export **red/green** color — **STILL BROKEN**: exports come out grayscale regardless of the on-screen colorized overlay. Root cause: `_render_export_image` in `odyssey_app/app.py:889` fetches each channel via `_render_single_channel` → `get_preview(channels="700")` which returns single-channel grayscale from the vendor server; `Image.open(...).convert("RGB")` produces `(v,v,v)` grayscale; `ImageChops.lighter(img700, img800)` on two grayscales is still grayscale. The on-screen canvas colorizes in JS (`renderChannelCanvas`), the server-side compositor does not. Fix: apply the 700→red / 800→green tint in `_render_export_image` before compositing (or fetch a pre-tinted preview if the vendor server supports it).
   - If B&W invert displayed → export B&W invert (invert path also needs verification)
   - (Channel overlay in B&W is loved — keep the toggle.)
   - Exported image should annotate **intensity setting per channel** (reinforced 2026-07-09).
   - (TIFF export path is covered separately in #1 — always raw, not the current view.)

10. **HAP antibody ID instead of catalogue number** (user feedback 2026-05-11) — HAP = Human and Animal Physiology (our chair group). Internal HAP antibody ID is enough on its own; HAP database maps it to vendor + catalogue + lot automatically. Form should accept HAP ID only.

11. **Split metadata entry: pre-scan minimum + during-scan rest** (user feedback 2026-05-11) — there's a ~5-minute wait during scanning that's currently wasted. Use it:
    - Pre-scan required (small set): project, scan name, user name
    - During-scan editable: everything else (samples, antibodies, notes)
    - Form stays interactive while scan runs

12. **PDF report export** (user feedback 2026-05-11, higher priority than #13) — current export is JSON; users want a human-friendly PDF showing the scan image + metadata (including intensity setting) in a readable layout. Replaces JSON for end users.

13. **Click-to-label lanes on image** (user feedback 2026-05-11, lower priority — "for later, lot of work") — user clicks in the displayed image to define lane positions + labels. Could be automated later. PDF report (#12) gets users most of the value sooner.

14. **Operator stamp — consistent format (initials only)** (surfaced during 2026-05-21 PLR talk prep) — the metadata stamp burned into exported images currently shows whatever the operator typed: sometimes first name first name, sometimes initials. Standardise on **initials only**, matching the folder-routing convention from #5. Same identifier should appear in: folder path, in-image stamp, JSON sidecar, TIFF metadata tag.

15. **PIDinst Handle in exported TIFF metadata** (surfaced 2026-05-21) — the presentation narrative claims "every TIFF self-identifies" via the PIDinst Handle URI. Confirm this is actually embedded in the raw TIFF export path (which is being rewritten under #1). Verify by opening an exported TIFF in ImageJ → Image → Show Info — the Handle URI should be present. Also consider a small human-readable stamp of the Handle in the image footer for cases where someone views the image without metadata-aware tools.

16. **Barcode / QR code stamped in image** (Rick's suggestion, 2026-05-21) — Rick mentioned that some imagers stamp a low-contrast greyscale **barcode** into the exported image as a unique per-scan identifier. We'd extend this to a **QR code** encoding the PIDinst Handle URI + scan ID. Properties: greyscale (doesn't compete with the scan content), small (image corner), low-contrast (visible to a phone camera but doesn't dominate visually), unique per scan. Bridges the gap between machine-readable TIFF metadata and human-scannable provenance — anyone with a phone can resolve the QR to the B2INST landing page.

17. **Reopen a previous project to change settings and re-export** (user feedback 2026-07-09) — right now every scan/experiment is one-shot: fill in settings → configure → scan → export. Users want to open a prior experiment (from history), edit its metadata (add a missed lane, correct an antibody vendor, adjust intensity annotation) and re-export the report/PNG without re-scanning. The raw TIFF stays canonical; only the presentation layer + metadata sidecar changes on re-export. Design: history row → "Reopen" → loads form fields from the JSON record, pulls channel bitmaps from cache/exports, re-runs export with the new metadata.

18. **Numbered lane strip on exported image + tidy sample table** (user feedback 2026-07-09) — user asked for lane info rendered as "een mooi tabelletje/lijstje met bv nummers boven de laantjes" — a small numbered strip drawn above the lane positions on the exported image, plus a clean sample table (name/treatment/µg) rendered below or beside. Simpler than #13 (click-to-label): lanes are already entered in the metadata form (`p700Target`, `laneCount`, sample rows), just need to draw them onto the export. Two things:
    - **In-image strip**: numbered header row above the gel lanes (lane 1, 2, 3, …) in a legible font, matching the crop or full width. Positions can start from equal spacing across the crop width, adjustable later.
    - **Sample table**: rendered in the export footer (below the image) and in the PDF report (#12). Currently the JSON has lanes; just needs a printed layout.
    - Precursor to #13 (which is click-to-position). This gets 80% of the value with 20% of the work.

19. **✅ SHIPPED 2026-07-09** — Transform record stamped into every presentation export (TIFF tag 270 + PNG `provenance_json` tEXt chunk). Fields: `brightness_700/800`, `contrast_700/800`, `bw`, `view`, `crop`, `tint`, `stretch`. Raw-path exports (`Normal TIFF`, `ImageJ TIFF`) mark `transforms: null` so tools know the file is quantifiable directly. Blotchiness fix comes from (1a) raw path; brightness delta is now reproducible from the stamped record. Original brief:

   **"Why does the exported image look brighter / blotchier than raw?"** (user feedback 2026-07-09, ties into #1 + #3) — user specifically flagged: check if signal processing between raw TIFF and exported presentation makes the image look different. **The lab user's 2026-07-09 evidence** identifies the two sources concretely:
    - **Blotchy background** = 16→8 bit quantization on the display canvas (covered by #1's raw-TIFF path)
    - **"Vlekkeriger" appearance** persists even at neutral brightness/contrast because 8-bit only has 256 grey levels; the raw 16-bit has 65 536. Fixed by #1 (raw path) + #2 (percentile stretch for display).
    - **Brightness gap** — `_apply_bc` (`odyssey_app/app.py:873`) uses `v' = cf * ((v + brightness) - 128) + 128` with `cf = (259 * (contrast + 255)) / (255 * (259 - contrast))`, same formula as the on-screen canvas. So a user with brightness>0 sees AND exports a brightened image; that's WYSIWYG, not a bug, but must be documented.
    - **Action**: every export (PNG + presentation-TIFF + raw-TIFF sidecar) records the applied transform as a machine-readable block in metadata: `{"brightness_700": +X, "contrast_700": +Y, "brightness_800": +A, "contrast_800": +B, "invert": bool, "bw": bool, "stretch": "off"|"percentile 0.5/99.5", "crop": [x,y,w,h] | null, "tint": "off"|"red_green"}`. Same block goes into (a) PNG tEXt chunks (already used by `PngInfo` at app.py:1044), (b) TIFF tag 270 (image description, alongside existing PIDinst identity), (c) the PDF report footer (#12). This makes the raw↔exported delta reconstructible from the file alone.
    - Overarching principle (user, 2026-07-09): **capture raw pixels, record every transform**. Same principle as #1's overarching note — this item is the "record every transform" half.

20. **Invert = black bands on white background** (user feedback 2026-07-09) — the current "invert" toggle likely flips something in RGB space, but the scientific reading users want is **B&W-invert**: raw signal (bright pixels = signal) shown as **dark pixels on a light background**, the classical Western-blot look. Applies whenever the B&W toggle is on:
   - Off + not-invert: red/green colored overlay (current)
   - On (B&W) + not-invert: bright bands on dark (current B&W)
   - On (B&W) + invert: **dark bands on white** ← this is what users want and how blots are conventionally shown in figures.
   - Coloured + invert is a niche case; skip unless a user asks.
   - Frontend: apply after B&W conversion in `drawChannelLayer` in index.html; backend: same in `_render_export_image` (before the tint step, or by inverting the greyscale mask). Verify the change is recorded in the transform record (#19 schema) as `"invert": true`.

21. **Form field placeholders — clearly mark as examples** (user feedback 2026-07-09) — the metadata form has placeholders like "Sample", "Condition", "#4370" that some users read as pre-filled values rather than examples they should overwrite. Make them unambiguous:
   - Prefix with **"e.g."** or **"fill: e.g."** so it's clear the field is expected to be filled, not left as-is.
   - For fields where the user's own identity flows in (scan name, project, operator), suggest a pattern rather than a random-looking placeholder: e.g. scan name → `e.g. VB260709` (initials + YYMMDD), operator → `e.g. VB` (initials), project → `e.g. NR3C1-timecourse`.
   - Sweep all `placeholder="..."` attributes in `odyssey_app/static/index.html` (form section) — currently ~20 of them. Consistent prefix + pattern per field.

## Worth deciding on

- **Operator profiles** — consequence of (5)+(6): one "operator" concept holding folder mapping, default group, required-vs-optional preference, possibly HAP-ID defaults.
- **Instrument status in UI** — motivated by (4): cooling/warming/ready surfaced explicitly.
- **Better error messaging** — needed before non-developers use it solo.
- **Privacy of researcher identity in metadata + stamps** (surfaced 2026-05-21) — do we need to inject any researcher name (first name OR initials) into exported images and metadata at all? When images are shared in slides, papers, on conference posters, that identifier travels with them. Decide: opt-in vs opt-out, initials-only minimum, fully anonymised mode, or operator-side preference. Not answered, just flagged.

## Resolved

2026-04-23 lab-session items all done: live preview toggle, export circular JSON, button reset, stop button, auto-image-load, zoom, channel cache + overlay, default group=odyssey, input defaults. Recent commits (phases 38–42) covered crop region, B&W mode, configure countdown, scan-name namespacing, theme tints.

**Why:** Two waves of real-user feedback (first hardware session 2026-04-23, demo + first solo user 2026-05-04/05-11). The TIFF-export + display + intensity-setting trio (1, 2, 3) is the most important — it affects whether scientific results from this app are trustworthy. The lab user's screenshot (2026-05-11) confirmed the TIFF export problem visually: raw 16-bit scanner TIFF is clean, our 8-bit RGB overlay TIFF is blotchy in the background despite same sharpness.
**How to apply:** (1)–(3) before anything else — correctness, not polish. Then (8) for solo-user robustness. (9) has an active bug (grayscale export) that's an easy fix — do that with the next round of #1 work. (4)–(7) and (10)–(12) are UX. (14)–(16) cluster around identity/provenance and pair naturally with the TIFF-export rewrite (1). (17)–(19) are 2026-07-09 additions: (17) reopen-project unlocks report-fix workflows without re-scanning; (18) numbered lane strip is the cheap precursor to (13); (19) pairs with (3) as a provenance note in the export. (13) is later.
