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
| 2026-11-18 | **Sam's answers** (relayed by the coordinating session, ~00:45 Eastern): deadline is a working consumer-facing app by noon Eastern today (17:00 UTC); content is monologue-driven social content, short films, ads, music videos, interviews/docs; audio is sometimes separate, sometimes camera only; takes are told apart by any of timecode/metadata, file names, clapperboard, or nothing; export is a render plus sequences for Resolve, Premiere and Final Cut; the PC has an NVIDIA RTX card; the built-in LLM is a local model on the PC, no API key, last resort only; test material is in his Drive folder "Video Editing Resources". | Sam | Sam |
| 2026-11-18 | Assumption: "consumer-facing" means intuitive for a non-technical person, still behind Sam's sign-in. Opening it to the public is a separate decision Sam hasn't made. | Coordinating session's reading of Sam's answer. | Coordinating session |
| 2026-11-18 | Sync order for separate audio: timecode, then waveform matching, then clap/slate; skipped when only camera audio exists. | Covers every case Sam described. | Coordinating session |
| 2026-11-18 | Proposed: sequence export through OpenTimelineIO, with FCPXML / FCP7 XML / EDL adapters. | One timeline model, three target editors. | Coordinating session (proposal) |
| 2026-11-18 | Models run on CUDA when available, CPU otherwise. | The PC has an NVIDIA RTX card; the cloud machine has none. | Coordinating session |
| 2026-11-18 | Sam's media (footage, audio, scripts from his Drive) is never committed to the repository. | Sam's instruction, via the coordinating session. | Sam |
| 2026-11-18 | Builder: picture grouping uses MobileCLIP2-S0 when installed, else a model-free colour signature; the LLM is called once, only to order scenes when there's no script, through an OpenAI-compatible URL (`VSE_ASSEMBLY_LLM_URL` / `LLM_BASE_URL`), and timecode order is kept when none is set. | From the builder's commit `bc8865e7` and `assembly/llm.py`. | Builder |
| 2026-11-18 | Testing is done in a Linux container (`cloud/Dockerfile`, `Dockerfile.test`) on generated media: 124×70 (16:9), 24 fps, 1 s clips without speech, dialogue takes as long as their line. Supersedes the earlier 12×12 test shoot. | Sam | Sam (via coordinator) |
| 2026-11-18 | The PC worker is stood down; the private production repo is not used. | Coordinator's call. | Coordinator |
| 2026-11-18 | Real clips from Sam's "culture clips collections" may be used once the container passes: downloaded one by one, downscaled to 140p, never committed. | Real footage without storing Sam's media. | Coordinator |
| 2026-11-18 | Guess: the chat panel is hidden behind a setting (off by default), not deleted. Answers the earlier open question unless Sam overrules. | Sam's plan says no chat; deleting working code is his call. | Coordinator / design worker |
| 2026-11-18 | PC answered: NVIDIA RTX 3050 (8 GB), 64 GB RAM. | Sam | Sam (via coordinator) |
| 2026-11-18 | Builder guess: long steps run as background jobs with job ids and polling. | The site can't hold a request open for minutes. | Builder |
| 2026-11-18 | Builder guess: a recorder file syncs to a clip only if it overlaps ≥60% of the clip and the raw-sound match is ≥0.3. | Avoids false pairings. | Builder |
| 2026-11-18 | Builder guess: in numbered ad scripts the speaker is NARRATOR, and text in [brackets] is on screen. | Sam's ad script layout. | Builder |
| 2026-11-18 | Builder guess: music assumes 4/4, cuts every 2 bars in instrumental parts and on the beat before each sung line. | Common music-video cutting. | Builder |
| 2026-11-18 | Builder guess: sequence export uses the official OpenTimelineIO adapters. | Maintained, standard. | Builder |
| 2026-11-18 | Design guess: confidence labels: Strong ≥0.8, Likely ≥0.5. | Simple wording for non-technical users. | Design worker |
| 2026-11-18 | Design guess: exports map Resolve → OTIO, Premiere → FCP7 XML, Final Cut → FCPXML; exports go to the Drive folder "latest avenue exports". | Each editor's most reliable import. | Design worker |
| 2026-11-18 | Design guess: the edit files live in a hidden `.latest-avenue` folder. | Keep the user's Drive tidy. | Design worker |
