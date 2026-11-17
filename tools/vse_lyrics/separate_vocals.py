#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Separate the vocal from the music of a song clip, so that lyrics can be timed on the voice
alone (``align_lyrics.py --vocals``). Music under the voice is the main cause of mistimed and
misheard words.

Uses Demucs (MIT licence; model "htdemucs"). It needs PyTorch, which needs Python 3.8-3.12 on
an Intel Mac, so it lives in its own environment (see doc/vse/lyric_video_workflow.md):

    /usr/local/bin/python3.11 -m venv stems-env
    stems-env/bin/pip install "torch==2.2.2" "torchaudio==2.2.2" "numpy<2" demucs soundfile
    stems-env/bin/python tools/vse_lyrics/separate_vocals.py song.wav -o song.vocals.wav

About 15 seconds per 17-second clip on a CPU. The model (80 MB) is downloaded on first use
into the folder named by --models-dir or the VSE_LYRICS_MODELS environment variable.
"""

import argparse
import os
import shutil
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("audio")
    parser.add_argument("-o", "--output", help="default: <audio>.vocals.wav")
    parser.add_argument("--models-dir", default=os.environ.get("VSE_LYRICS_MODELS"))
    parser.add_argument("--model", default="htdemucs")
    args = parser.parse_args()

    if args.models_dir:
        # Demucs fetches its model through the Hugging Face hub or torch hub.
        os.environ.setdefault("HF_HOME", os.path.join(args.models_dir, "hf"))
        os.environ.setdefault("TORCH_HOME", os.path.join(args.models_dir, "torch"))
    import demucs.separate

    output = args.output or os.path.splitext(args.audio)[0] + ".vocals.wav"
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(output))) as tmp:
        demucs.separate.main(["--two-stems", "vocals", "-n", args.model, "-d", "cpu", "-o", tmp, args.audio])
        name = os.path.splitext(os.path.basename(args.audio))[0]
        shutil.move(os.path.join(tmp, args.model, name, "vocals.wav"), output)
    print("wrote " + output)


if __name__ == "__main__":
    sys.exit(main())
