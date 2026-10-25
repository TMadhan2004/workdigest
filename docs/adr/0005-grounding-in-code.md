# ADR 0005: Grounding is enforced in code, and facts are written by code

**Status:** accepted (24 Oct 2026)

**Context.** Trust is the product. A digest that invents "644/644", claims "done" for unpushed work, or uses a ticket key that was never touched does more harm than no digest. Asking the model to be careful is not enough.

**Decision.**
1. **Code writes pure facts.** This covers the header, the Jira list, the Testing and Commits blocks (latest full run per suite, commits per repo with branch links), the PR/branch list, and "day N of 5".
2. **The LLM writes narrative only.** It returns JSON statements that cite evidence ids.
3. **`grounding.py` checks every statement.** Each number, ticket key, URL and SHA must appear in the cited evidence. "Done" sections need a fact (commit, test or branch), not only an agent's claim. Dates and times are exempt, because they are commitments, not measurements.
4. **Failures stay visible.** They render as `[unverified: confirm …]` and appear in the gap panel. They are never silently dropped.

**Consequences.** Some correct but uncited statements are flagged and need a quick confirmation. A no-LLM mode works offline and never invents anything.
