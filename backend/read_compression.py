"""Compress large JSON reads without recompressing photos or streamed media."""
from starlette.middleware.gzip import GZipMiddleware


class ReadCompressionMiddleware:
    paths = frozenset(('/v1/bootstrap', '/v1/library', '/v1/updates', '/v1/feed',
                       '/v1/jobs', '/v1/bookmarks', '/v1/review', '/v1/me/sources'))

    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1000, compresslevel=4)

    async def __call__(self, scope, receive, send):
        app = self.compressed if scope['type'] == 'http' and scope.get('path') in self.paths else self.app
        await app(scope, receive, send)
