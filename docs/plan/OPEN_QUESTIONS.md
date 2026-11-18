# Open questions for Sam

Only Sam can answer these. When one is answered, move the answer into
[DECISIONS.md](DECISIONS.md) (and [PRODUCT.md](PRODUCT.md) if it changes the product) and
delete it here.

1. **What do you shoot?** Short films, ads, music videos, interviews? How much footage per
   project (hours, number of clips)?
2. **Cameras and codecs.** Which cameras, and what files: H.264, H.265, ProRes, BRAW? (BRAW
   is not read by stock ffmpeg.)
3. **Sound.** Is sound recorded separately (dual-system)? If so we need a sync step: by
   timecode, or by waveform matching. Do the cameras record scratch audio?
4. **Slates and naming.** Do you use slates (clapper / smart slate)? How are files named?
   Is scene/take in the file name or metadata?
5. **Script format.** Are scripts Final Draft-style PDFs (standard screenplay layout)?
   Other formats (Fountain, FDX) available?
6. **Where do sequences go?** Premiere, Resolve or Final Cut? Is OpenTimelineIO acceptable,
   or does a specific format (XML/EDL/AAF/FCPXML) matter?
7. **The Windows PC.** Which graphics card (and its memory) and how much RAM? This decides
   which models run fast enough.
8. **Who uses it?** Just you, or clients / crew too?
9. **Which LLM is the built-in one?** The existing assistant defaults to Qwen2.5 7B Instruct
   (4-bit, llama.cpp) — keep that, or another?
10. **The existing chat panel** on `/edit` (`cloud/agent.py`): your plan says no chat. Remove
    it, hide it, or leave it?
11. **A test case.** Can you provide a small sample shoot (footage, audio, script) plus the
    "right answer" cut, so the assembly cut can be scored against it?
