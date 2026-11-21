"""Supervise the classic PEDIT origin and an optional authenticated quick tunnel."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_port(port: int):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'Port {port} is already in use. Close the existing PEDIT session first.') from exc


def wait_ready(server, url: str, nonce: str, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f'PEDIT exited before it was ready (code {server.returncode}).')
        try:
            with urllib.request.urlopen(url + '/health', timeout=1) as response:
                health = json.load(response)
                if health.get('status') == 'ok' and health.get('launch_id') == nonce:
                    return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError('PEDIT did not become ready. Cloudflare was not started.')


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote', action='store_true', help='Start authenticated Cloudflare access')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    remote = args.remote
    if not remote and sys.stdin.isatty():
        print('[1] Local editing (default)\n[2] Authenticated remote access')
        remote = input('Choice [1]: ').strip() == '2'
    server = tunnel = keep_awake = None
    try:
        if not 1 <= args.port <= 65535:
            raise RuntimeError('Port must be in 1-65535.')
        check_port(args.port)
        cloudflared = shutil.which('cloudflared') if remote else None
        if remote and not cloudflared:
            raise RuntimeError('Cloudflare is not installed. Install cloudflared before using remote mode.')
        url = f'http://127.0.0.1:{args.port}'
        env = os.environ.copy()
        env.pop('PEDIT_REMOTE_PASSWORD', None)
        nonce = secrets.token_hex(16)
        env['PEDIT_LAUNCH_ID'] = nonce
        if remote:
            env['PEDIT_REMOTE_PASSWORD'] = secrets.token_urlsafe(24)
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'classic.app:app',
                                   '--host', '127.0.0.1', '--port', str(args.port)],
                                  cwd=ROOT, env=env)
        wait_ready(server, url, nonce)
        print(f'PEDIT ready at {url}', flush=True)
        if remote:
            # Do not forward the password to the tunnel or sleep-prevention process.
            print('Remote username: pedit', flush=True)
            print(f"Remote password: {env['PEDIT_REMOTE_PASSWORD']}", flush=True)
            print('Open the new HTTPS URL printed by Cloudflare. Keep this terminal open.', flush=True)
            if sys.platform == 'darwin' and shutil.which('caffeinate'):
                keep_awake = subprocess.Popen(['caffeinate', '-i', '-w', str(server.pid)])
            tunnel = subprocess.Popen([cloudflared, 'tunnel', '--url', url])
        elif not args.no_browser:
            import webbrowser
            webbrowser.open(url)
        while server.poll() is None and (tunnel is None or tunnel.poll() is None):
            time.sleep(0.25)
        if server.poll() is not None:
            raise RuntimeError(f'PEDIT stopped (code {server.returncode}); closing the tunnel.')
        raise RuntimeError(f'Cloudflare stopped (code {tunnel.returncode}); closing PEDIT.')
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        stop(tunnel)
        stop(server)
        stop(keep_awake)


if __name__ == '__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, interrupt)
    raise SystemExit(main())
