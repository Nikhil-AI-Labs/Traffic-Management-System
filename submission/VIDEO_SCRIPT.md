# Demo Video Script — 2:45

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

**Target:** 2–3 minutes · MP4 · 1080p · screen recording with voice-over
**Actual runtime:** 2:45 of narration at a natural pace (~150 wpm), leaving
headroom inside the 3:00 limit.

---

## Before you record

```bash
cd E:\Researcher\erakshak
streamlit run src/dashboard.py          # http://localhost:8501
```

**Checklist**
- [ ] Set `FLUID_ROOT` so the max-pressure track has data — **without it, five
      of the thirteen views show a "data not found" callout on camera**
- [ ] Browser at 1920×1080, zoom 100%, full screen (`F11`)
- [ ] Hide bookmarks bar; close other tabs
- [ ] Pre-load each view once so nothing is computing mid-take
- [ ] Sidebar set to **Webster — Surat network**, view **Network**
- [ ] OBS / Xbox Game Bar recording at 1080p 30 fps

**Tone.** Explain, don't sell. The strongest thing about this project is that it
says what it cannot do — deliver that plainly and it lands harder than
enthusiasm would.

---

## Shot list

### 00:00 – 00:18 · Cold open — the problem
**On screen:** Network view, full dashboard. Slow scroll to the congestion
ranking.

> "Surat has a CCTV network already watching its streets. Problem statement 08
> asks us to turn it into a traffic system — measure density, optimise the
> signals, find the bottlenecks. We analysed seven cameras: twenty-one zones,
> two-and-a-half thousand measurement windows. But the first thing the footage
> told us was that the obvious approach would have been confidently wrong."

---

### 00:18 – 00:48 · The discovery
**On screen:** Switch to **How it works** view. Scroll to the occlusion chart —
the detections-per-camera bar chart.

> "This is detection count per camera. Rokadiya Hanuman — a free-flowing
> arterial — gives seven and a half thousand detections. Mahidharpura, the worst
> gridlock in the dataset, gives four hundred and sixty-three.
>
> At extreme density, vehicles stop being separable and the detector's recall
> collapses. So a system that equates more detections with more congestion would
> rank Surat's worst street among its emptiest. And every number would look
> plausible.
>
> Our fix is a texture-based occupancy index — edge density, normalised against
> absolute bounds measured across all seven cameras. It has no notion of an
> object, so occlusion can't defeat it. Mahidharpura now flags four hundred and
> fifty-two of four hundred and fifty-three windows as saturated, and ranks
> correctly as the most congested site."

---

### 00:48 – 01:28 · The operational result
**On screen:** **Signal timing** view. Let the `1.01 → 0.93` headline sit for a
beat. Then drag the *Existing fixed-timer cycle* slider so the audience sees it
recompute live. Then scroll to the cycle-sweep chart.

> "Here's the operational output. The existing fixed timer runs a hundred-and-
> twenty-second cycle with equal green splits, and that drives the busier
> approach to a degree of saturation of one-point-oh-one — over capacity, where
> the queue never clears within the cycle.
>
> Webster's optimal plan brings that to nought-point-nine-three. That's the
> claim that survives any delay model, and it's the one that matters.
>
> Two independent checks. Sweeping cycle length puts the empirical delay minimum
> at a hundred and thirty seconds, against Webster's closed form of a hundred and
> thirty-five point six. And the optimal plan equalises saturation across both
> approaches — which is the known optimality condition for Webster green
> allocation, so we're not just asserting the implementation is right."

*(If asked about the small delay figure, the honest line is: "delay falls three
point eight percent — the saturation crossing is the real result, and we quote
the smaller number rather than the flattering one.")*

---

### 01:28 – 01:50 · Evidence, not just numbers
**On screen:** **Bottlenecks** view. Hover one critical alert so the evidence
tooltip shows. Then **Outlook** briefly.

> "Thirty-one bottleneck alerts, each carrying the evidence that raised it and a
> specific recommendation — so an engineer can check the reasoning rather than
> trust it. This one is a standing queue: sustained stopped-fraction above
> threshold, with the recommendation to check downstream before retiming,
> because if the blockage is downstream, adding green here just moves the queue.
>
> And twenty-one zone forecasts with prediction intervals — the band widens
> where the history is thin, rather than pretending to precision."

---

### 01:50 – 02:25 · The part we're proudest of
**On screen:** Switch the sidebar toggle to **Max-pressure — FLUID validation**.
Let the whole dashboard change. Land on **Scene** — the aerial with shaded
queues.

> "Now the part we think matters most. Max-pressure is the throughput-optimal
> signal controller. It needs the queue on the approach *and* the queue on the
> link it discharges into.
>
> No Surat camera watches a receiving link. So that term simply is not in our
> data. We could have set it to zero — but that doesn't give a degraded
> max-pressure, it gives greedy queue control, which Varaiya's own paper proves
> fails to stabilise demands max-pressure handles.
>
> So our implementation refuses. On Surat it reports 'greedy-queue' and declines
> to name a phase.
>
> Instead we went and got a dataset that has the missing term — FLUID, fourteen
> drone recordings of three real signalised intersections. This is the picture
> the Surat cameras structurally cannot produce: every approach and every
> receiving link in one frame. Replayed against the real signals, those
> fixed-time plans spend forty-one to sixty-six percent of their green on a phase
> whose queue had already cleared. Max-pressure, on the same measured queues,
> does that one-and-a-half to sixteen percent of the time."

---

### 02:25 – 02:45 · Close on the honest note
**On screen:** **Head-to-head** view — the three-scenario comparison table.

> "And we'll say the unflattering part too. In simulation, max-pressure is
> *worse* than a fixed timer on steady demand — it pays an inter-green for a
> benefit that isn't there. It wins when demand shifts between approaches, and
> wins big when something downstream backs up: thirty-one percent less delay.
>
> So the system doesn't offer it as an upgrade. It tells the operator which
> regime their junction is actually in, and recommends accordingly.
>
> Two controllers. Two datasets. Neither borrowing from the other — and a test
> that fails if that ever stops being true."

---

## Timing summary

| Segment | Duration | Cumulative |
|---|---|---|
| Cold open — the problem | 0:18 | 0:18 |
| The discovery | 0:30 | 0:48 |
| Operational result | 0:40 | 1:28 |
| Evidence | 0:22 | 1:50 |
| Max-pressure & FLUID | 0:35 | 2:25 |
| Honest close | 0:20 | 2:45 |

---

## If you need to cut to 2:00

Drop **Evidence** (0:22) entirely, and trim the operational segment to the
`1.01 → 0.93` headline plus one verification check (saves ~0:20). That lands at
**2:03** and keeps both discoveries and the max-pressure argument, which are the
parts that differentiate the project.

## If you have room to 3:00

Add 15 s on **Site detail** — zone tiles, scene overlay, movement heatmap and
the speed–density diagram — to show the measurement layer directly rather than
only its conclusions.

---

## Recording notes

- **Do not narrate the UI.** Say what the number means, not "here you can see a
  chart showing…".
- **Pause after each headline figure.** `1.01 → 0.93` needs a beat to land.
- **Move the cursor deliberately.** Slow drags read as confident; fast jitter
  reads as lost.
- **One take per segment**, then join — far easier than one clean 3-minute run.
- **Record system audio off**, mic only. No background music: it competes with
  the numbers.
- If a view is slow to compute, cut the dead air out rather than talking over
  the spinner.
