"""Published hotel change events for client SSE.

Единая точка вызова после `db.commit()` во всех partner-мутациях, меняющих
данные, видимые клиенту через public API. Клиент, подписанный на
`/api/v1/public/hotels/{slug}/events`, получит `{"type":"refresh"}` и
перезапросит state. См. TBB-36 / playbook §9.4.
"""
from app.core import pubsub


async def publish_hotel_change(hotel_id: int) -> None:
    await pubsub.publish_refresh(hotel_id)
