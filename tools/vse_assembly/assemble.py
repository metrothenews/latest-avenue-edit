#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Assembly cut from the command line. See doc/vse/assembly_workflow.md.

    python3 tools/vse_assembly/assemble.py assemble MEDIA --script SCRIPT.pdf -o cut.edit.json

Same commands as ``python -m vse_standalone.assembly`` (run with --help).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "scripts", "modules"))

from vse_standalone.assembly.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
