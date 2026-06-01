"""Partner schemas — split-out по доменам (см. карту #49 этап 1, 2026-06-01).

Re-export всего публичного API сохраняет совместимость с прежними
импортами `from app.schemas.partner import ...`.
"""
from app.schemas.partner.audit import AuditEntryView
from app.schemas.partner.availability import (
    AvailabilityBatchUpdate,
    AvailabilityRowIn,
    AvailabilityRowOut,
)
from app.schemas.partner.bookings import (
    PartnerBookingPostpaySet,
    PartnerBookingView,
    WalkinBookingCreate,
)
from app.schemas.partner.clients import (
    ClientLookup,
    ClientPartnerView,
    ClientUpdate,
)
from app.schemas.partner.hotels import (
    ChecklistAction,
    ChecklistItem,
    HotelCreate,
    HotelDashboard,
    HotelPartnerView,
    HotelStats,
    HotelUpdate,
)
from app.schemas.partner.rooms import (
    RoomCreate,
    RoomFlatView,
    RoomPartnerView,
    RoomUpdate,
)
from app.schemas.partner.services import (
    ServiceCreate,
    ServicePartnerView,
    ServiceUpdate,
)
from app.schemas.partner.staff import (
    OwnerAccess,
    StaffCreate,
    StaffInviteAccept,
    StaffInviteCreate,
    StaffInviteView,
    StaffPerms,
    StaffUpdate,
    StaffView,
)

__all__ = [
    "AuditEntryView",
    "AvailabilityBatchUpdate",
    "AvailabilityRowIn",
    "AvailabilityRowOut",
    "ChecklistAction",
    "ChecklistItem",
    "ClientLookup",
    "ClientPartnerView",
    "ClientUpdate",
    "HotelCreate",
    "HotelDashboard",
    "HotelPartnerView",
    "HotelStats",
    "HotelUpdate",
    "OwnerAccess",
    "PartnerBookingPostpaySet",
    "PartnerBookingView",
    "RoomCreate",
    "RoomFlatView",
    "RoomPartnerView",
    "RoomUpdate",
    "ServiceCreate",
    "ServicePartnerView",
    "ServiceUpdate",
    "StaffCreate",
    "StaffInviteAccept",
    "StaffInviteCreate",
    "StaffInviteView",
    "StaffPerms",
    "StaffUpdate",
    "StaffView",
    "WalkinBookingCreate",
]
