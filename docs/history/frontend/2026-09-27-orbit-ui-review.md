# ORBIT frontend review — 2026-09-27

## Pre-change baseline (rows 0–1)

- Plan: `fba2268a958e968ec6cca6d2637dd30a5e1300cab66a60538122961db3fced60`.
- Route: `http://127.0.0.1:8000/`; CUA Chrome tab `1084304133`; viewport `1440×900`, reset after capture.
- Filesystem and served bytes matched by C6: `index.html` `97f0f3e5cb69bba9eb33c73568ba8b6c55285fe4b81ae3b91d9bef49361eef11`; `styles.css` `a6303cbd3c3a456eb581a72a4c540d2506ee937aa3c71750bb1f3a8e74e695cb`; `app.js` `cf640008525028315168e311066f32cbb4c57a25a7a8211901d6260ce91200cf`; `globe.js` `6c777c41580f9088675a42a3961be5166ba67c3ec13901780153bd48a109947b`.
- Test identities: `ui.test.cjs` `78f8d2c73dba038ebd61f410b5c9a1223957a53152942e0626aad4cd3f842ddc`; `globe.test.cjs` `27e37fc808e7a2d0b6e30dec2c990302c64ca8e10dae374a177771e0b9da2bf3`.
- A landing CUA capture: compact header, large headline and four primary presets on the left; unboxed interactive Earth dominates the right; no result rail before a query; disabled free-text is below the fold.
- B LAX/SNA CUA capture: request `44411765-63ee-4ee8-ad42-650bff539bf5`; LAX and SNA selected on Earth; result shows `congestion`, 2024, backend summary, and the operational table. Visible examples include LAX cancellation `0.81%`, diversion `0.29%`, departure delay `13.51 min`, taxi-out `18.66 min`, and SNA cancellation `0.93%`.
- C SFO CUA capture: request `8107b8bb-972f-4cd1-bb6d-e9f197cdad48`; SFO selected on Earth; result shows `sfo pressure`, 2024, passenger total `25,288,609`, seats `30,369,317`, passenger growth `3.96%`, and the real monthly series table. No seat-growth claim was visible.
- Baseline images were emitted in the CUA run but no durable local screenshot-file API was exposed; this is an observation baseline, not final acceptance evidence.
- 200% zoom: **UNVERIFIED**. The dedicated target-tab API exposed no Chrome menu or visible percentage; prior target-tab shortcuts gave no measurable change. No native Chrome window was claimed. This remains an open final-acceptance item and does not block implementation.
