"""Authenticate opt-in live probes using the panel's current startup token."""
from html.parser import HTMLParser
import urllib.request
from urllib.parse import urlsplit

class _TokenParser(HTMLParser):
    token = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'webmemu-token':
            self.token = attrs.get('content')

_tokens = {}

def panel_open(request, timeout=5):
    if isinstance(request, str):
        request = urllib.request.Request(request)
    url = urlsplit(request.full_url)
    base = f'{url.scheme}://{url.netloc}'
    if base not in _tokens:
        with urllib.request.urlopen(base + '/', timeout=timeout) as response:
            parser = _TokenParser()
            parser.feed(response.read().decode('utf-8'))
        if not parser.token:
            raise RuntimeError('Panel oturum anahtarı bulunamadı; sunucuyu güncelleyin.')
        _tokens[base] = parser.token
    request.add_header('X-WebMEmu-Token', _tokens[base])
    return urllib.request.urlopen(request, timeout=timeout)
