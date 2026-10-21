"""Run the authorized public-frontier crawl and validate its final export."""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import traceback

from crawl import Crawler, now
from validate import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--interval', type=float, default=0.35)
    parser.add_argument('--wait-pid', type=int, default=0,
                        help='On Windows, wait for an existing checkpoint run to exit before resuming.')
    args = parser.parse_args()
    folder = pathlib.Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    status_path = folder / 'background_job.json'
    status = {'pid': os.getpid(), 'started_at': now(), 'state': 'running',
              'output': str(folder), 'stop_method': 'Create a file named STOP in the output directory.'}
    def save():
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    try:
        if args.wait_pid:
            if os.name != 'nt':
                raise RuntimeError('--wait-pid is supported only on Windows')
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, args.wait_pid)
            if handle:
                status.update(state='waiting_for_checkpoint', waiting_for_pid=args.wait_pid)
                save()
                try:
                    result = kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
                    if result != 0:
                        raise RuntimeError('Could not wait for checkpoint process: ' + str(result))
                finally:
                    kernel.CloseHandle(handle)
            elif ctypes.get_last_error() not in (0, 87):
                raise ctypes.WinError(ctypes.get_last_error())
            status.update(state='running', resumed_at=now())
            save()
        crawler = Crawler(argparse.Namespace(output=str(folder), interval=args.interval,
                           max_apps=0, max_seconds=0, max_pages=1000, export_only=False))
        with (folder.parent / 'icons.stdout.log').open('a', encoding='utf-8') as icon_stdout, \
             (folder.parent / 'icons.stderr.log').open('a', encoding='utf-8') as icon_stderr:
            icon_worker = subprocess.Popen([sys.executable,
                str(pathlib.Path(__file__).with_name('download_icons.py')),
                '--output', str(folder)], stdout=icon_stdout, stderr=icon_stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        status['icon_worker_pid'] = icon_worker.pid
        save()
        reason = crawler.run()
        status.update(state='finishing_icons')
        save()
        icon_exit_code = icon_worker.wait()
        validate(folder)
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        state = 'complete' if reason == 'public_frontier_exhausted' else 'stopped'
        if state == 'complete' and (icon_exit_code or any(v for k, v in manifest.get('icon_counts', {}).items() if k != 'done')):
            state = 'complete_with_errors'
        status.update(state=state,
                      finished_at=now(), stop_reason=reason,
                      exported_app_count=manifest['exported_app_count'], validation_passed=True,
                      icon_worker_exit_code=icon_exit_code, icon_counts=manifest.get('icon_counts', {}))
    except Exception as error:
        status.update(state='error', finished_at=now(), error=str(error))
        traceback.print_exc()
    finally:
        save()


if __name__ == '__main__':
    main()
