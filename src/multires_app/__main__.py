"""Command dispatch shared by the workspace and installed console entry."""
import sys


def main():
    if len(sys.argv)>1 and sys.argv[1]=='tools':
        from multires_app.tools.runner import main as tools_main
        return tools_main(sys.argv[2:])
    from multires_app.cli.multires import main as cli_main
    return cli_main()


if __name__=='__main__':
    raise SystemExit(main())
