import time
from shared.repositories.base import BaseRepository

class ExpenseRepository(BaseRepository):
    async def add_expense(self, user_id: int, amount: float, category: str, description: str, receipt_id: str = None):
        now = time.time()
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO expenses (user_id, amount, category, description, receipt_file_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, amount, category, description, receipt_id, now)
            )
            await db.commit()
