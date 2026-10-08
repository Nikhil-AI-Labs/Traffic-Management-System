# Demo Video Script — 2:55

E-Rakshak Hackathon 2026 · PS-08 · *Surat Traffic Intelligence*

**Target:** 2–3 minutes · MP4 · 1080p · screen recording with voice-over
**Actual runtime:** 2:55 of narration at a natural pace (~150 wpm), inside the
3:00 limit.

---

## Before you record

```powershell
cd E:\E-RAKSHAK\Traffic-Management-System
$env:FLUID_ROOT = "E:\Conflict"
.\.venv\Scripts\python.exe src\api.py     # http://127.0.0.1:8000
```

**Checklist**
- [ ] `FLUID_ROOT` set — **without it the five max-pressure views show a
      "data not found" callout on camera**
- [ ] `npm run build` in `web/` after any front-end change, then restart the server
- [ ] Browser at 1920×1080, zoom 100%, full screen (`F11`), light theme
- [ ] Internet on — the map tiles come from OpenStreetMap
- [ ] Close other heavy programs: live detection wants the CPU and ~1 GB of RAM
- [ ] Open each view once so nothing is computing mid-take
- [ ] Start live detection once and stop it, so the model is already loaded
- [ ] Live monitor at **2×**; press **Restart** (↺) as the take begins — the
      first alerts fire 6 s into the clips, about 3 s later
- [ ] Signal view scrolled to the top
- [ ] OBS / Xbox Game Bar recording at 1080p 30 fps

**Tone.** Explain, don't sell. The strongest thing about this project is that it
says what it cannot do — deliver that plainly and it lands harder than
enthusiasm would.

---

## Shot list

### 00:00 – 00:22 · Cold open — the control room
**On screen:** **Live monitor**, replay at 2×, just restarted. The map with
coloured camera pins, the camera list with sparklines moving. After ~3 s four
critical alerts fire and the Gopi Talav and Mahidharpura pins start pulsing.
Click Gopi Talav on the map.

> "This is Surat's CCTV network as a traffic control room. Seven cameras on one
> screen — each one's level of service on the map, its congestion trend, and
> alerts the moment they fire. Gopi Talav and Mahidharpura just went critical:
> their evidence completed its third sustained window, so they were raised —
> quoting only what was known at that instant, never the rest of the clip."

---

### 00:22 – 00:45 · The real pipeline, live
**On screen:** Focus **Rokadiya Hanuman**. Press **Run live detection**. Boxes
appear on the video, the LIVE badge, the frame rate in the status bar.

> "That was a replay of the recorded measurements. This is the real thing:
> YOLO11, fine-tuned on Indian CCTV, with ByteTrack and our metrics engine,
> running at wall-clock speed on this laptop's CPU. When it can't keep up it
> skips frames rather than falling behind, so the picture is always now. It also
> watches every track: a vehicle standing still in a lane that's flowing gets
> boxed and raised — a breakdown, or an illegal stop."

---

### 00:45 – 01:10 · The discovery
**On screen:** **How it works**. Scroll to the occlusion chart.

> "The footage taught us something first. Rokadiya Hanuman, a flowing arterial:
> seven and a half thousand detections. Mahidharpura, the worst gridlock in the
> dataset: four hundred and sixty-three. When vehicles pack too tight, the
> detector stops seeing them — so 'more detections, more congestion' would rank
> Surat's worst street among its emptiest. We measure texture-based occupancy
> instead, which occlusion can't defeat, and Mahidharpura ranks first."

---

### 01:10 – 01:48 · The signal — and how long the fix lasts
**On screen:** **Signal timing**. Let `1.01 → 0.93` sit. Scroll to **How long
does the retiming last?** — the headroom chart. Then in **Scenario**, set
*Capacity lost on* → Rokadiya Hanuman and drag to 10%.

> "The fixed timer drives the busier approach to a degree of saturation of
> one-point-oh-one — over capacity, the queue never clears. Webster's plan brings
> it to nought-point-nine-three.
>
> Then the question an engineer actually asks: how long does that last?
> Webster's closed form answers it. This junction has eight percent of headroom —
> about a year and a half at five percent traffic growth. And one parked bus near
> the stop line" *(drag)* "takes the best possible plan to nought-point-nine-eight.
> Past that, the answer is road space, not a better cycle."

---

### 01:48 – 02:10 · Alerts become work
**On screen:** **Incidents**. Acknowledge a critical incident with the note
"PCR van dispatched"; the sidebar badge drops by one. Then **Network** →
**Network briefing**; show the printable page for two seconds.

> "Every alert becomes work. Acknowledge it, resolve it, note what was done —
> the log keeps who and when, survives a restart, and exports for the shift
> handover. And the whole network goes on a printable briefing for the morning
> meeting."

---

### 02:10 – 02:42 · Max-pressure — validated where the data allows
**On screen:** Switch the sidebar to **Max-pressure — FLUID drone**. Land on
**Scene**, the aerial with shaded queues. Then **Validation**.

> "Max-pressure is the throughput-optimal controller, but it needs the queue on
> the link each movement discharges into — and no Surat camera watches one.
> Setting it to zero gives greedy queue control, which Varaiya's own paper proves
> unstable. So on Surat our implementation refuses to name a phase.
>
> Instead we validated it on FLUID: drone recordings of real intersections where
> every link is in frame. The real fixed-time signals spend forty-one to
> sixty-six percent of their green on a phase with nothing left to clear.
> Max-pressure: one and a half to sixteen."

---

### 02:42 – 02:55 · Close on the honest note
**On screen:** **Head-to-head** table.

> "And it's worse than a fixed timer on steady demand. So we don't sell it as an
> upgrade — the system tells the operator which regime a junction is in. Every
> number you've seen is recomputed by a test that fails if the code and this
> story ever disagree."

---

## Timing summary

| Segment | Duration | Cumulative |
|---|---|---|
| Cold open — the control room | 0:22 | 0:22 |
| The real pipeline, live | 0:23 | 0:45 |
| The discovery | 0:25 | 1:10 |
| Signal and headroom | 0:38 | 1:48 |
| Incidents and briefing | 0:22 | 2:10 |
| Max-pressure and FLUID | 0:32 | 2:42 |
| Honest close | 0:13 | 2:55 |

---

## If you need to cut to 2:00

Drop **Incidents and briefing** (0:22) and the second paragraph of
**Max-pressure** (0:15), and trim the discovery to its first two sentences plus
the fix (saves ~0:12). That lands near **2:05** and keeps the control room, the
live pipeline, the signal result and the refusal — the parts that differentiate
the project.

---

## Recording notes

- **Do not narrate the UI.** Say what the number means, not "here you can see a
  chart showing…".
- **Pause after each headline figure.** `1.01 → 0.93` and "a year and a half"
  each need a beat to land.
- **Move the cursor deliberately.** Slow drags read as confident; fast jitter
  reads as lost.
- **One take per segment**, then join — far easier than one clean 3-minute run.
- **Record system audio off**, mic only; the alert chime is optional and
  competes with the voice.
