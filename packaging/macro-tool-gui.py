import sys

if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    from macrotool.cli import main as cli_main
    sys.exit(cli_main(sys.argv[2:]))

from macrotool.app import main

sys.exit(main())
