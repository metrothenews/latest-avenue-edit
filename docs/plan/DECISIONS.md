# Decisions and guesses

Sam said: when a dilemma comes up, guess, and record the guess here so he can overrule it in
one place. Newest at the bottom. Mark an entry **overruled** rather than deleting it.

| Date | Decision or guess | Why | Made by |
|---|---|---|---|
| 2026-11-18 | Small open-source base models first; LoRA fine-tuning only later, if they miss on Sam's footage. | Sam wrote "LoRA-based"; fine-tuning needs his footage and a right-answer cut to measure against. | Plan (from Sam's notes) |
| 2026-11-18 | Script supervisor notes are not built now; the editing script reserves fields for slate, circled flag and notes. When added, a circled take wins, otherwise scoring decides. | Sam's notes: don't build yet, document contingencies. | Plan (from Sam's notes) |
| 2026-11-18 | Build order starts with script parsing and the assembly cut as command-line tools, before UI and Drive. | Testable without the website or Drive; the core value. | Plan |
| 2026-11-18 | The editing script is stored as JSON. | Proposed stack; easy to diff, test and pass over `/api/rpc`. | Plan (to be confirmed by builder) |
| 2026-11-18 | Guess: the new pipeline does not use or depend on the existing chat-based editing assistant (`cloud/agent.py`, chat panel on `/edit`). It is left in place for now; whether to remove the chat panel is an open question. | Sam's plan says the LLM has no chat feature, but removing existing working code is his call. | Documentation worker |
