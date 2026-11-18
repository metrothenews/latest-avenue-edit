# Open questions for Sam

Only Sam can answer these. When one is answered, move the answer into
[DECISIONS.md](DECISIONS.md) (and [PRODUCT.md](PRODUCT.md) if it changes the product) and
delete it here. Answered on 2026-11-18: content types, separate sound, how takes are told
apart, target editors, GPU (NVIDIA RTX), built-in LLM (local, no API key), test material.

1. **Going public?** For now the app stays behind your sign-in. Should it ever open to
   other people (clients, crew, the public)?
2. **How much footage per project?** Hours, number of clips. This decides processing time.
3. **Cameras and codecs.** Which cameras, and which files: H.264, H.265, ProRes, BRAW?
   (BRAW is not read by stock ffmpeg.)
4. **Script format.** Are scripts Final Draft-style PDFs? The "Ad Scripts for Assignment N"
   in your Drive are in a "docs" folder: PDFs or Google Docs?
5. **The PC in detail.** Which RTX card, how much graphics memory, how much RAM?
6. **Which local model?** The existing assistant defaults to Qwen2.5 7B Instruct (4-bit,
   llama.cpp). Keep that?
7. **The existing chat panel** on `/edit` (`cloud/agent.py`): your plan says no chat.
   Remove it, hide it, or leave it?
8. **A "right answer" cut.** Your test material has footage and scripts. Is there a finished
   edit of any of it to score the assembly against?
9. **Is the repository public?** The coordinating session says it is; the README says it is
   private. Either way, your media is not committed.
