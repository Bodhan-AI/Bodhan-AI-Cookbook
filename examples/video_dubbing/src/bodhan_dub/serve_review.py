"""Loopback-only development server with audio byte-range support."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


class ReviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.wav': 'audio/wav', '.mp3': 'audio/mpeg'}

    def send_head(self):
        self.byte_range = None
        requested = self.headers.get('Range')
        path = self.translate_path(self.path)
        if not requested or not os.path.isfile(path):
            return super().send_head()
        stream = open(path, 'rb')
        size = os.fstat(stream.fileno()).st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
        try:
            if not match or not any(match.groups()) or not size:
                raise ValueError
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size-1) if last else size-1
            else:
                count = int(last)
                if not count:
                    raise ValueError
                start, end = max(0, size-count), size-1
            if not 0 <= start <= end < size:
                raise ValueError
        except ValueError:
            stream.close()
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        self.byte_range = (start,end)
        stream.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(end-start+1))
        self.end_headers()
        return stream

    def end_headers(self):
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Cache-Control','no-cache')
        super().end_headers()

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1]-self.byte_range[0]+1
        while remaining:
            chunk = source.read(min(remaining,65536))
            if not chunk:
                break
            try:
                outputfile.write(chunk)
            except (BrokenPipeError,ConnectionResetError):
                break
            remaining -= len(chunk)


def main():
    parser=argparse.ArgumentParser(description='Serve only an existing review run on localhost.')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    run=args.run.expanduser().resolve()
    if not run.is_dir() or not (run/'review.html').is_file():
        parser.error('Choose a run folder containing review.html.')
    if not 1 <= args.port <= 65535:
        parser.error('Use a valid port.')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(ReviewHandler,directory=str(run)))
    print(f'Local review: http://127.0.0.1:{args.port}/review.html',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
