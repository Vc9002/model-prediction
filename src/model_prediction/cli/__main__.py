"""`python -m model_prediction.cli` entry point.

The package itself (model_prediction.cli) is a re-export shim that keeps
`from model_prediction.cli import _X` working for tests and tooling while
the implementation lives in the cli/ submodules.
"""

from model_prediction.cli import main

if __name__ == "__main__":
    main()
