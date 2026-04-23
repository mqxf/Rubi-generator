#!/usr/bin/env python3
from __future__ import annotations

import sys

from rubi_gto.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["autofill-manual-fixes", *sys.argv[1:]]))
