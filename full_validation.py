"""Run all release checks; see --help. Skips/xfail/missing Panda fail the gate."""
from validation_tools.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
