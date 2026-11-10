"""允许通过 `python -m tools.eval_suite` 运行。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
