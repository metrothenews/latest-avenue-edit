# Open questions for Sam

Only Sam can answer these. When one is answered, move the answer into
[DECISIONS.md](DECISIONS.md) (and [PRODUCT.md](PRODUCT.md) if it changes the product) and
delete it here. Answered on 2026-11-18: content types, separate sound, how takes are told
apart, target editors, GPU (NVIDIA RTX), built-in LLM (local, no API key), test material. Later: the PC (RTX 3050, 8 GB; 64 GB RAM); the chat panel (hidden
behind a setting, off by default: a guess Sam may overrule, see DECISIONS.md).

1. **Going public?** For now the app stays behind your sign-in. Should it ever open to
   other people (clients, crew, the public)?
2. **How much footage per project?** Hours, number of clips. This decides processing time.
3. **Cameras and codecs.** Which cameras, and which files: H.264, H.265, ProRes, BRAW?
   (BRAW is not read by stock ffmpeg.)
4. **Script format.** Are scripts Final Draft-style PDFs? The "Ad Scripts for Assignment N"
   in your Drive are in a "docs" folder: PDFs or Google Docs?
5. **Which local model?** The existing assistant defaults to Qwen2.5 7B Instruct (4-bit,
   llama.cpp). Keep that?
6. **A "right answer" cut.** Your test material has footage and scripts. Is there a finished
   edit of any of it to score the assembly against?
7. **Should the repository be public?** It is public now (cloned anonymously at 06:26 UTC);
   the README still says private. Keep it public or make it private? Either way, your media
   is not committed.
8. **Two Drive integrations?** Should the editor's existing Drive import/export elsewhere be
   reconciled with the builder's `assembly/drive.py`?
