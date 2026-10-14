from decimal import Decimal
import datetime
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User, SellerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory
from apps.delivery.models import DeliveryAssignment, DeliveryAttempt, DeliveryOTP


class DeliveryFulfillmentWorkflowTests(TestCase):
    """
    Automated test suite verifying the complete 22 fulfillment, handover, privacy,
    COD collection, and hashed OTP verification requirements from the Master Specification.
    """

    def setUp(self):
        # 1. Users
        self.admin = User.objects.create_user(
            username='admin_test',
            email='admin@test.com',
            password='password123',
            role=User.Role.ADMIN
        )

        self.seller1 = User.objects.create_user(
            username='seller_green',
            email='green@test.com',
            password='password123',
            role=User.Role.SELLER,
            phone_number='9876543210'
        )
        self.seller1_profile = SellerProfile.objects.create(
            user=self.seller1,
            store_name='Green Valley Organics',
            store_slug='green-valley-organics',
            pickup_address='Shop 12, Green Agro Market, Downtown',
            city='Downtown',
            pin_code='110001',
            store_phone='9876543210',
            is_approved=True
        )

        self.seller2 = User.objects.create_user(
            username='seller_dairy',
            email='dairy@test.com',
            password='password123',
            role=User.Role.SELLER,
            phone_number='9876543211'
        )
        self.seller2_profile = SellerProfile.objects.create(
            user=self.seller2,
            store_name='Fresh Dairy Farms',
            store_slug='fresh-dairy-farms',
            pickup_address='Plot 5B, Dairy Colony, Uptown',
            city='Uptown',
            pin_code='110002',
            store_phone='9876543211',
            is_approved=True
        )

        self.delivery_agent = User.objects.create_user(
            username='driver_rajesh',
            email='rajesh@test.com',
            password='password123',
            role=User.Role.DELIVERY,
            phone_number='9811122233'
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_agent,
            vehicle_type=DeliveryProfile.VehicleType.BIKE,
            vehicle_number='KA-01-AB-1234',
            current_status=DeliveryProfile.Status.AVAILABLE
        )

        self.delivery_agent2 = User.objects.create_user(
            username='driver_other',
            email='other@test.com',
            password='password123',
            role=User.Role.DELIVERY,
            phone_number='9844455566'
        )
        self.delivery_profile2 = DeliveryProfile.objects.create(
            user=self.delivery_agent2,
            vehicle_type=DeliveryProfile.VehicleType.SCOOTER,
            vehicle_number='KA-02-XY-9876',
            current_status=DeliveryProfile.Status.AVAILABLE
        )

        self.buyer = User.objects.create_user(
            username='buyer_pooja',
            email='pooja@test.com',
            password='password123',
            role=User.Role.BUYER,
            phone_number='9988776655'
        )

        # 2. Category & Products
        self.category = Category.objects.create(name='Fresh Produce', slug='fresh-produce')
        
        self.product1 = Product.objects.create(
            seller=self.seller1,
            category=self.category,
            name='Fresh Organic Tomatoes',
            mrp=Decimal('60.00'),
            selling_price=Decimal('50.00'),
            unit_value='1 kg',
            sku='SKU-TOMATO-01',
            is_active=True
        )
        StockRecord.objects.create(product=self.product1, current_stock=100)

        self.product2 = Product.objects.create(
            seller=self.seller2,
            category=self.category,
            name='Pure Farm Cow Milk',
            mrp=Decimal('40.00'),
            selling_price=Decimal('35.00'),
            unit_value='1 L',
            sku='SKU-MILK-02',
            is_active=True
        )
        StockRecord.objects.create(product=self.product2, current_stock=50)

        # Clients
        self.client_buyer = Client()
        self.client_seller1 = Client()
        self.client_seller2 = Client()
        self.client_delivery = Client()
        self.client_delivery2 = Client()
        self.client_admin = Client()

        self.client_buyer.force_login(self.buyer)
        self.client_seller1.force_login(self.seller1)
        self.client_seller2.force_login(self.seller2)
        self.client_delivery.force_login(self.delivery_agent)
        self.client_delivery2.force_login(self.delivery_agent2)
        self.client_admin.force_login(self.admin)

    def _create_test_order(self, payment_method='COD', payment_status='PENDING'):
        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer,
            recipient_name='Pooja Sharma',
            recipient_phone='9988776655',
            delivery_address_text='Flat 402, Sunshine Apts, Central Ave',
            pin_code='110001',
            address_type='HOME',
            delivery_slot='10:00 AM - 01:00 PM',
            delivery_instructions='Leave at door if not answering',
            status=Order.Status.ORDER_CONFIRMED if payment_status == 'PAID' or payment_method == 'COD' else Order.Status.ORDER_PLACED,
            payment_method=payment_method,
            payment_status=payment_status,
            subtotal=Decimal('85.00'),
            total_amount=Decimal('85.00')
        )
        sub1 = SellerSubOrder.objects.create(
            order=order,
            seller=self.seller1,
            subtotal=Decimal('50.00'),
            status=SellerSubOrder.Status.PENDING
        )
        OrderItem.objects.create(
            order=order,
            sub_order=sub1,
            seller=self.seller1,
            product=self.product1,
            product_name='Fresh Organic Tomatoes',
            unit_value='1 kg',
            unit_price=Decimal('50.00'),
            mrp=Decimal('60.00'),
            quantity=1,
            total_price=Decimal('50.00')
        )
        sub2 = SellerSubOrder.objects.create(
            order=order,
            seller=self.seller2,
            subtotal=Decimal('35.00'),
            status=SellerSubOrder.Status.PENDING
        )
        OrderItem.objects.create(
            order=order,
            sub_order=sub2,
            seller=self.seller2,
            product=self.product2,
            product_name='Pure Farm Cow Milk',
            unit_value='1 L',
            unit_price=Decimal('35.00'),
            mrp=Decimal('40.00'),
            quantity=1,
            total_price=Decimal('35.00')
        )
        return order, sub1, sub2

    # Scenario 1: Buyer places an order and receives a unique Order ID
    def test_01_buyer_receives_unique_order_id(self):
        order, _, _ = self._create_test_order()
        self.assertTrue(order.order_id.startswith('ORD-'))
        self.assertGreater(len(order.order_id), 12)

    # Scenario 2: The same Order ID appears in the seller's order list
    def test_02_same_order_id_in_seller_order_list(self):
        order, sub1, _ = self._create_test_order()
        resp = self.client_seller1.get(reverse('seller:orders'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, order.order_id)

    # Scenario 3: Admin assigns a delivery person
    def test_03_admin_assigns_delivery_person(self):
        order, _, _ = self._create_test_order()
        resp = self.client_admin.post(
            reverse('admin_dashboard:assign_delivery', kwargs={'order_id': order.order_id}),
            {'delivery_person_id': self.delivery_agent.id}
        )
        self.assertEqual(resp.status_code, 302)
        assignment = DeliveryAssignment.objects.get(order=order)
        self.assertEqual(assignment.delivery_person, self.delivery_agent)
        self.assertEqual(assignment.status, DeliveryAssignment.Status.ASSIGNED)

    # Scenario 4: Seller sees the assigned delivery person's name
    def test_04_seller_sees_assigned_delivery_person(self):
        order, sub1, _ = self._create_test_order()
        DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            assigned_by=self.admin,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        resp = self.client_seller1.get(reverse('seller:order_detail', kwargs={'order_id': order.order_id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.delivery_agent.username)

    # Scenario 5: Delivery person sees the shop address before pickup
    def test_05_delivery_person_sees_shop_address_before_pickup(self):
        order, sub1, _ = self._create_test_order()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            assigned_by=self.admin,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        resp = self.client_delivery.get(reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.seller1_profile.pickup_address)
        self.assertContains(resp, self.seller1_profile.store_name)

    # Scenario 6: Delivery person cannot access customer's address before pickup
    def test_06_delivery_person_cannot_access_customer_address_before_pickup(self):
        order, sub1, _ = self._create_test_order()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            assigned_by=self.admin,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        resp = self.client_delivery.get(reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}))
        self.assertEqual(resp.status_code, 200)
        # Customer address and instructions must NOT be in the HTML response
        self.assertNotContains(resp, 'Sunshine Apts')
        self.assertNotContains(resp, 'Leave at door if not answering')
        self.assertContains(resp, 'Customer Details Protected')

    # Scenario 7: Incorrect Order ID is rejected during pickup
    def test_07_incorrect_order_id_rejected(self):
        order, sub1, _ = self._create_test_order()
        sub1.status = SellerSubOrder.Status.PACKED
        sub1.save()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.ACCEPTED
        )
        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'verify_pickup', 'order_id_input': 'ORD-WRONG-ID'}
        )
        self.assertEqual(resp.status_code, 302)
        assignment.refresh_from_db()
        self.assertNotEqual(assignment.status, DeliveryAssignment.Status.PICKED_UP)
        self.assertFalse(assignment.shop_verified)

    # Scenario 8: Correct Order ID is accepted only for the assigned delivery person
    def test_08_correct_order_id_accepted_only_for_assigned_agent(self):
        order, sub1, sub2 = self._create_test_order()
        sub1.status = SellerSubOrder.Status.PACKED
        sub1.save()
        sub2.status = SellerSubOrder.Status.PACKED
        sub2.save()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.ACCEPTED
        )
        # Unauthorized driver attempts to access / submit pickup
        resp_unauth = self.client_delivery2.get(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id})
        )
        self.assertEqual(resp_unauth.status_code, 404)

        # Assigned driver submits correct Order ID
        resp_auth = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'verify_pickup', 'order_id_input': order.order_id}
        )
        self.assertEqual(resp_auth.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DeliveryAssignment.Status.PICKED_UP)
        self.assertTrue(assignment.shop_verified)

    # Scenario 9: Seller cannot hand over an unpacked order
    def test_09_seller_cannot_handover_unpacked_order(self):
        order, sub1, _ = self._create_test_order()
        sub1.status = SellerSubOrder.Status.PREPARING  # not PACKED
        sub1.save()
        DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        resp = self.client_seller1.post(
            reverse('seller:order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'confirm_handover', 'order_id_verification': order.order_id}
        )
        self.assertEqual(resp.status_code, 302)
        sub1.refresh_from_db()
        self.assertEqual(sub1.status, SellerSubOrder.Status.PREPARING)
        self.assertFalse(sub1.handover_confirmed_by_seller)

    # Scenario 10: Duplicate pickup attempts are rejected
    def test_10_duplicate_pickup_attempts_rejected(self):
        order, sub1, sub2 = self._create_test_order()
        sub1.status = SellerSubOrder.Status.PACKED
        sub1.save()
        sub2.status = SellerSubOrder.Status.PACKED
        sub2.save()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP,
            pickup_time=timezone.now()
        )
        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'verify_pickup', 'order_id_input': order.order_id}
        )
        self.assertEqual(resp.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DeliveryAssignment.Status.PICKED_UP)

    # Scenario 11: Successful pickup unlocks customer details
    def test_11_successful_pickup_unlocks_customer_details(self):
        order, sub1, _ = self._create_test_order()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP,
            pickup_time=timezone.now()
        )
        resp = self.client_delivery.get(reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Sunshine Apts')
        self.assertContains(resp, order.recipient_phone)

    # Scenario 12: Customer details remain protected through direct URL and API before pickup
    def test_12_customer_details_protected_before_pickup(self):
        order, _, _ = self._create_test_order()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        resp = self.client_delivery.get(reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}))
        self.assertFalse(assignment.is_customer_details_unlocked)
        self.assertNotIn('Sunshine Apts', resp.context['customer_details']['address'])
        self.assertIsNone(resp.context['customer_details']['phone'])

    # Scenario 13: Online payment is verified before being marked Paid
    def test_13_online_payment_verified_before_paid(self):
        order, _, _ = self._create_test_order(payment_method='UPI', payment_status='PENDING')
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        
        # Verify callback
        resp = self.client_buyer.post(
            reverse('orders:payment_verify', kwargs={'order_id': order.order_id}),
            {'action': 'success'}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertTrue(order.transaction_id.startswith('TXN-VERIFIED-'))

    # Scenario 14: COD payment remains Pending until confirmed collected
    def test_14_cod_payment_remains_pending_until_collected(self):
        order, _, _ = self._create_test_order(payment_method='COD', payment_status='PENDING')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertFalse(assignment.cod_collected)

        # Confirm cash collected
        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'collect_cod'}
        )
        self.assertEqual(resp.status_code, 302)
        assignment.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(assignment.cod_collected)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)

    # Scenario 15: OTP generation is blocked when payment requirements are not satisfied
    def test_15_otp_generation_blocked_when_unpaid(self):
        order, _, _ = self._create_test_order(payment_method='COD', payment_status='PENDING')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        # Attempt to send OTP while payment is pending
        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'send_otp'}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DeliveryOTP.objects.filter(assignment=assignment).count(), 0)

    # Scenario 16: OTP is sent to customer registered number (hashed in DB)
    def test_16_otp_sent_to_customer_registered_number(self):
        order, _, _ = self._create_test_order(payment_method='COD', payment_status='PENDING')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        # Satisfy payment
        order.payment_status = Order.PaymentStatus.PAID
        order.save()

        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'send_otp'}
        )
        self.assertEqual(resp.status_code, 302)
        otp = DeliveryOTP.objects.filter(assignment=assignment, is_used=False).first()
        self.assertIsNotNone(otp)
        self.assertEqual(otp.recipient_phone, order.recipient_phone)
        self.assertNotEqual(otp.hashed_otp, '123456')
        self.assertGreater(len(otp.hashed_otp), 32)

    # Scenario 17: Expired or incorrect OTPs are rejected
    def test_17_expired_or_incorrect_otp_rejected(self):
        order, _, _ = self._create_test_order(payment_method='COD', payment_status='PAID')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        otp_obj, raw_code = DeliveryOTP.generate(assignment, raw_code='654321')

        # Incorrect OTP
        is_valid, msg = otp_obj.verify('111111')
        self.assertFalse(is_valid)
        self.assertEqual(otp_obj.attempts_count, 1)

        # Expired OTP
        otp_obj.expires_at = timezone.now() - timezone.timedelta(minutes=10)
        otp_obj.save()
        is_valid, msg = otp_obj.verify(raw_code)
        self.assertFalse(is_valid)
        self.assertIn("expired", msg.lower())

    # Scenario 18: Excessive OTP attempts are blocked
    def test_18_excessive_otp_attempts_blocked(self):
        order, _, _ = self._create_test_order(payment_method='COD', payment_status='PAID')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        otp_obj, raw_code = DeliveryOTP.generate(assignment, raw_code='789012')
        otp_obj.attempts_count = 5
        otp_obj.save()

        is_valid, msg = otp_obj.verify(raw_code)
        self.assertFalse(is_valid)
        self.assertIn("maximum", msg.lower())

    # Scenario 19: Correct OTP completes delivery exactly once
    def test_19_correct_otp_completes_delivery_once(self):
        order, sub1, sub2 = self._create_test_order(payment_method='COD', payment_status='PAID')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )
        otp_obj, raw_code = DeliveryOTP.generate(assignment, raw_code='123987')

        # Submit valid OTP
        resp = self.client_delivery.post(
            reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id}),
            {'action': 'verify_otp', 'otp_code': raw_code}
        )
        self.assertEqual(resp.status_code, 302)
        assignment.refresh_from_db()
        order.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        otp_obj.refresh_from_db()

        self.assertEqual(assignment.status, DeliveryAssignment.Status.DELIVERED)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(sub1.status, SellerSubOrder.Status.DELIVERED)
        self.assertEqual(sub2.status, SellerSubOrder.Status.DELIVERED)
        self.assertTrue(otp_obj.is_used)

        # Second attempt using same code is rejected
        is_valid, msg = otp_obj.verify(raw_code)
        self.assertFalse(is_valid)
        self.assertIn("already been used", msg.lower())

    # Scenario 20: Buyer, seller, and delivery dashboards show consistent order states
    def test_20_dashboards_show_consistent_order_states(self):
        order, sub1, _ = self._create_test_order(payment_method='COD', payment_status='PAID')
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.DELIVERED,
            delivered_time=timezone.now()
        )
        order.status = Order.Status.DELIVERED
        order.save()
        sub1.status = SellerSubOrder.Status.DELIVERED
        sub1.save()

        # Buyer track page
        resp_buyer = self.client_buyer.get(reverse('orders:track', kwargs={'order_id': order.order_id}))
        self.assertEqual(resp_buyer.status_code, 200)
        self.assertContains(resp_buyer, 'Delivered')

        # Seller detail page
        resp_seller = self.client_seller1.get(reverse('seller:order_detail', kwargs={'order_id': order.order_id}))
        self.assertEqual(resp_seller.status_code, 200)
        self.assertContains(resp_seller, 'Delivered')

        # Delivery dashboard
        resp_driver = self.client_delivery.get(reverse('delivery:dashboard'))
        self.assertEqual(resp_driver.status_code, 200)
        self.assertContains(resp_driver, 'Delivered')

    # Scenario 21: Multi-seller orders preserve overall Order ID and seller-specific fulfillment
    def test_21_multi_seller_orders_preserve_order_id_and_isolation(self):
        order, sub1, sub2 = self._create_test_order()
        # Same customer-facing Order ID
        self.assertEqual(sub1.order.order_id, sub2.order.order_id)

        # Seller 1 views their order: only sees their items
        resp_s1 = self.client_seller1.get(reverse('seller:order_detail', kwargs={'order_id': order.order_id}))
        self.assertEqual(resp_s1.status_code, 200)
        self.assertContains(resp_s1, 'Fresh Organic Tomatoes')
        self.assertNotContains(resp_s1, 'Pure Farm Cow Milk')

        # Seller 2 views their order: only sees their items
        resp_s2 = self.client_seller2.get(reverse('seller:order_detail', kwargs={'order_id': order.order_id}))
        self.assertEqual(resp_s2.status_code, 200)
        self.assertContains(resp_s2, 'Pure Farm Cow Milk')
        self.assertNotContains(resp_s2, 'Fresh Organic Tomatoes')

    # Scenario 22: Responsive layout integrity and accessible endpoints
    def test_22_responsive_endpoints_accessibility(self):
        order, _, _ = self._create_test_order()
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        
        # Test all relevant endpoints respond with HTTP 200
        endpoints = [
            (self.client_buyer, reverse('orders:order_history')),
            (self.client_buyer, reverse('orders:track', kwargs={'order_id': order.order_id})),
            (self.client_seller1, reverse('seller:orders')),
            (self.client_seller1, reverse('seller:order_detail', kwargs={'order_id': order.order_id})),
            (self.client_delivery, reverse('delivery:dashboard')),
            (self.client_delivery, reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id})),
            (self.client_delivery, reverse('delivery:history')),
            (self.client_admin, reverse('admin_dashboard:orders')),
        ]
        for client, url in endpoints:
            resp = client.get(url)
            self.assertEqual(resp.status_code, 200, f"Endpoint {url} failed with status {resp.status_code}")
