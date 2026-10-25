# ADR 0006: Flutter app architecture

**Status:** accepted (24 Oct 2026)

**Context.** The app is the review surface, used briefly each evening. It must be fast, readable offline, accessible, and never hold anything secret.

**Decision.**
- **Layers.** Screens use Riverpod providers and controllers. Controllers use repositories. Repositories use a `CloudSource` interface: Supabase in the app, an in-memory fake in tests. Widgets never call Supabase directly.
- **Hand-written immutable models instead of freezed/json_serializable.** There is no `build_runner` step, so builds are reproducible and reviews show the real code. The parsing is tested against a real exported digest.
- **A JSON snapshot cache (shared_preferences) instead of Drift.** The requirement is to show the last digest and a clear state when sync fails. A read-through cache does that without native SQLite. Writes go to the cloud only.
- **Every Mac action is a job row** (`generation_jobs`) polled until done. The phone never runs the LLM or holds Cursor, Atlassian or service-role credentials.
- **Sharing uses the system share sheet** (`share_plus`), never `wa.me` links, which break on long text. Nothing is sent automatically.
- **Design.** Material 3 with one seed colour. `DigestColors` provides semantic state colours per theme. Spacing is on an 8-point grid. All motion comes from `Motion`, so reduced-motion settings turn it off (`MotionSize`, and `RevealIn` without timers).

**Consequences.** The app needs the Mac worker for any generation. The trade-offs are in docs/limitations.md.
