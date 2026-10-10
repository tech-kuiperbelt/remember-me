# Sample mockups — visual references only

Files:
- `01_light_retrieval.png` — Retrieval / Light
- `02_light_memory.png` — Memory / Light
- `03_dark_retrieval.png` — Retrieval / Dark
- `04_dark_memory.png` — Memory / Dark

**Use these for composition, spacing density, dark/light palette cues and placement of Memory/Retrieval selector only.** Do not treat mockup values, charts, icons and text labels as product requirements.

### Critical corrections for Codex
1. The current accepted Shell is authoritative. Do not introduce new navigation or redesign header/branding.
2. At a given viewport the wide Memory/Retrieval segmented control must have **the exact same x/y position, width and height** in all four states. Selection is a soft neutral-grey fill, not bright blue. Retrieval alone shows time presets at right; Memory leaves that area blank without recentering its switch.
3. The sample Retrieval overview appears with invented growth metrics; **do not implement deltas**. Use the single panel / three actual API values.
4. Several sample images use a five-star **4.2 / 5** rating — WRONG. Real feedback uses **-10..+10** and labels Positive (>0), Neutral (=0), Negative (<0).
5. Memory samples sometimes show Top 5/10/20 — WRONG. Accepted options: **5 / 30 / 100**, independent by metadata dimension.
6. Memory samples sometimes show 7-day growth percentages — WRONG. Memory is snapshot-only.
7. Never add fake Memory Activity chart, Total Tokens, Active Memories, or other not-agreed metrics.
8. All numbers, chart dates, percentages, names and lists are fictional demonstration data. Use real API data only and follow null/empty/error rules.
9. Light and Dark must share **exact layout**. Dark images are not permission to use alternate sizing/spacing.
10. The accepted written design takes precedence over any visual ambiguity.
