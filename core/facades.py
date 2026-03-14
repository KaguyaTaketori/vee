import logging

from config import get_allowed_users
from core.ratelimit import rate_limiter
from core.logger import log_user
from core.strategies import StrategyFactory

logger = logging.getLogger(__name__)


class DownloadFacade:
    """Facade for orchestrating the download process.
    
    Handles cross-cutting concerns:
    - Authorization check
    - Rate limiting check
    - Logging
    - Strategy selection and execution
    """
    
    @staticmethod
    async def process_download_request(query, url: str, callback_data: str, context, processing_msg) -> tuple[bool, str | None]:
        """Process download request through the facade.
        
        Args:
            query: Telegram callback query
            url: URL to download
            callback_data: Callback data determining download type (e.g., "download_audio", "quality_1080")
            context: Telegram callback context
            processing_msg: Message to update with progress
            
        Returns:
            Tuple of (success: bool, error_msg_key: str | None)
            If success is False, error_msg_key contains the i18n key for the error message.
        """
        user = query.from_user
        user_id = user.id
        
        allowed_users = get_allowed_users()
        if user_id not in allowed_users:
            logger.warning(f"Unauthorized user {user_id} attempted to download {url}")
            return False, "unauthorized"
        
        can_download, rate_limit_msg = rate_limiter.check_limit(user_id)
        if not can_download:
            logger.warning(f"User {user_id} blocked by rate limit: {rate_limit_msg}")
            return False, "rate_limit_exceeded"
        
        log_user(user, f"download_request:{callback_data}")
        
        strategy_key = DownloadFacade._map_callback_to_strategy(callback_data)
        if not strategy_key:
            logger.error(f"Unknown callback_data: {callback_data}")
            return False, "unknown_download_type"
        
        strategy = StrategyFactory.get(strategy_key)
        if not strategy:
            logger.error(f"No strategy found for key: {strategy_key}")
            return False, "unknown_download_type"
        
        try:
            await strategy.execute(query, url, processing_msg, context)
            return True, None
        except Exception as e:
            logger.error(f"Strategy execution failed: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False, "download_failed"
    
    @staticmethod
    def _map_callback_to_strategy(callback_data: str) -> str | None:
        """Map callback_data to strategy key.
        
        Examples:
            "download_audio" -> "download_audio"
            "download_video" -> "download_video" 
            "download_thumbnail" -> "download_thumbnail"
            "quality_1080" -> "video_1080"
            "quality_best" -> "video_best"
            "spotify" -> "spotify"
        """
        if callback_data in ("download_audio", "download_video", "download_thumbnail", "spotify"):
            return callback_data
        
        if callback_data.startswith("quality_"):
            format_id = callback_data.replace("quality_", "")
            return f"video_{format_id}"
        
        if callback_data.startswith("video_"):
            return callback_data
        
        return None
