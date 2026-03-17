from aiohttp import web
from services.container import services


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "queue_depth": services.queue.get_total_queued(),
        "active_tasks": services.queue.get_active_count(),
    })

def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    return app
