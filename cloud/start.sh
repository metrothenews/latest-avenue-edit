#!/bin/bash
# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Container entry point: the authenticated gateway, the only public port. It starts the
# editor itself (editor.sh) when the owner first connects.

export VSE_DATA="${VSE_DATA:-/data}"
exec python3 "$(dirname "$0")/gateway.py"
