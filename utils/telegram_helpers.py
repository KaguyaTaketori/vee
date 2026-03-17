from telegram import User

def user_log_args(user: User) -> dict:
    return {
        "user_id": user.id,
        "username": user.username or "N/A",
        "name": f"{user.first_name} {user.last_name or ''}".strip(),
    }
