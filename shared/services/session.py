class UserSession:
    
    _KEY_URL    = "pending_url_{}"
    _KEY_CACHE  = "cached_file_{}"

    @staticmethod
    def set_pending(context, user_id: int, url: str, cached_path: str = None):
        context.user_data[UserSession._KEY_URL.format(user_id)] = url
        context.user_data[UserSession._KEY_CACHE.format(user_id)] = cached_path

    @staticmethod
    def get_pending_url(context, user_id: int) -> str | None:
        return context.user_data.get(UserSession._KEY_URL.format(user_id))

    @staticmethod
    def get_cached_file(context, user_id: int) -> str | None:
        return context.user_data.get(UserSession._KEY_CACHE.format(user_id))

    @staticmethod
    def clear(context, user_id: int):
        context.user_data.pop(UserSession._KEY_URL.format(user_id), None)
        context.user_data.pop(UserSession._KEY_CACHE.format(user_id), None)
