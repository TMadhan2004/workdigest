# ADR 0008: Realtime by refresh, rules as evidence filters, Aurora design

**Status:** accepted (25 Oct 2026)

**Context.** The app only updated on open or refresh. Personal sessions leaked into digests. The user wanted their own rules ("don't include these PRs"), and a more engaging look.

**Decision.**
- **Realtime by refresh.** Tables are published to Supabase Realtime, and RLS filters every change. Providers don't merge change payloads into state. They refresh themselves when their table changes (`refreshOn`), so a change reuses the existing loading, cache and offline paths, keeps the current data on screen, and can't drift from the database. Channels stay joined for the app's lifetime. On resume the app resyncs.
- **A heartbeat on its own thread** with a live status (idle, drafting, syncing, offline). "Offline" is published inside the SIGTERM handler, because a stop during a long task would otherwise be killed before it unwinds.
- **Hidden sessions are a read-time filter.** Every evidence query skips them, and nothing is deleted. The hidden list and My rules are small, so each tick mirrors them whole. Restores and deletes need no change events.
- **My rules filter the evidence pack, not the text.** The Mac reads free text into exclusions (PR, age, ticket, repo) plus an optional instruction. The LLM's reading is checked against known repos. The user confirms before a rule applies. Filtering happens before evidence is numbered, so an excluded item can't reach the prompt, the deterministic PR section, the branch list or the header. Instructions are best effort and labelled so.
- **Aurora design.** Dark-first indigo, violet and pink, with Manrope bundled under the OFL. Glass is used for floating surfaces only, and turns solid under reduced motion or high contrast. The background is static. A floating nav uses `extendBody`, so pages get the bar's height as bottom padding. Every page paints its own backdrop so transitions stay opaque. Custom semantics are containers, so a screen reader gets one item per control.

**Consequences.** A burst of changes causes one debounced re-read (300 ms). Rule reading needs the Mac. Goldens and widget tests pin the design.
