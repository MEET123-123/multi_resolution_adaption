"""Run maintenance commands without exposing their physical Python paths."""
import argparse
import importlib
import sys

COMMANDS={
    'saas-store':'saas_store',
    'batch-convert':'batch_convert',
    'theme-adapter':'theme_adapter',
    'iterate-anchor-layout':'iterate_anchor_layout',
    'iterate-aesthetics':'iterate_aesthetics',
    'repack-layouts':'repack_layouts',
    'refresh-previews':'refresh_previews',
    'refresh-media-validation':'refresh_media_validation',
    'refresh-background-cover':'refresh_background_cover',
    'compact-layout-artifacts':'compact_layout_artifacts',
    'audit-assets':'audit_assets',
    'release-readiness':'release_readiness',
    'validate-tablet-theme':'validate_tablet_theme',
}


def main(argv=None):
    parser=argparse.ArgumentParser(description='项目维护工具；在项目根目录运行。用 <工具名> --help 查看参数。')
    parser.add_argument('tool',choices=sorted(COMMANDS))
    options=list(sys.argv[1:] if argv is None else argv)
    if not options or options[0] in {'-h','--help'}:
        parser.print_help();return 0
    if options[0]=='saas-store':
        module='multires_app.service.saas_store'
    else:
        parsed=parser.parse_args(options[:1]);module='multires_app.tools.'+COMMANDS[parsed.tool]
    previous=sys.argv
    try:
        sys.argv=[module,*options[1:]]
        result=importlib.import_module(module).main()
    finally:sys.argv=previous
    return result if isinstance(result,int) else 0
