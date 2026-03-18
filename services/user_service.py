# Backward compatibility - re-export from shared/services
from shared.services.user_service import (
    track_user,
    get_allowed_users,
    save_allowed_users,
    get_all_users_info,
    get_user_display_name,
    get_user_display_names_bulk,
    cleanup_temp_files,
    warm_user_lang,
    set_user_language,
    format_user_display,
)
