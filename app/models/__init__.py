# importing every model here so Base.metadata knows all tables (alembic needs this)
from app.models.user import (Role, Permission, RolePermission, User, RefreshToken, RevokedToken,
                             EmailVerification, UserActivity, ProfileChangeHistory, Address)
from app.models.catalog import (Category, Brand, Product, ProductVariant, ProductFile, Attribute,
                                ProductAttributeValue, PriceHistory)
from app.models.inventory import (Warehouse, Inventory, StockMovement, InventoryTransfer, TransferItem,
                                  ThresholdHistory, LowStockAlert)
from app.models.cart import Cart, CartItem, WishlistItem
from app.models.pricing import (Coupon, CouponUsage, Promotion, PromotionHistory, TaxRule, TaxHistory,
                                ShippingMethod)
from app.models.order import Order, OrderItem, OrderStatusHistory, OrderCancellation
from app.models.payment import Payment, PaymentTransaction, WebhookEvent, Refund, CODRule, CODCollection
from app.models.shipping import ShippingProvider, Shipment, TrackingEvent, Delivery, DeliveryAttempt
from app.models.returns import ReturnRequest, ReturnItem, Exchange, ExchangeHistory
from app.models.engagement import Review, ProductQuestion, ProductAnswer, SupportTicket, TicketMessage
from app.models.notification import Notification, MessageProvider, OutboundMessage
from app.models.supplier import (Supplier, SupplierHistory, SupplierProduct, PurchaseOrder,
                                 PurchaseOrderItem, SupplierInvoice)
from app.models.system import AuditLog, BackgroundJob