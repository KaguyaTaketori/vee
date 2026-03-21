from database.db import get_db
from shared.services.search_service import index_bills_bulk

async def reindex_all():
    async with get_db() as db:
        async with db.execute("SELECT * FROM bills") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    
    docs = [{
        "id":          r["id"],
        "user_id":     r["user_id"],
        "amount":      r["amount"],
        "currency":    r["currency"],
        "category":    r["category"],
        "description": r["description"],
        "merchant":    r["merchant"],
        "bill_date":   r["bill_date"],
        "receipt_url": r["receipt_url"],
        "created_at":  r["created_at"],
    } for r in rows if r["id"] is not None]
    
    await index_bills_bulk(docs)
