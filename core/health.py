from aiohttp import web

async def health_handler(request: web.Request) -> web.Response:
    from services.queue import download_queue
    return web.json_response({
        "status": "ok",
        "queue_depth": download_queue.get_total_queued(),
        "active_tasks": download_queue.get_active_count(),
    })

def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    return app

