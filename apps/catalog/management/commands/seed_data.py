from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile
from apps.catalog.models import Category, Product, ProductImage
from apps.inventory.models import StockRecord, StockMovement
from apps.orders.models import Order, OrderItem, SellerSubOrder, Address
from apps.delivery.models import DeliveryAssignment


class Command(BaseCommand):
    help = 'Seeds initial demonstration data for all 4 roles, categories, products, inventory, and sample orders'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Beginning grocery marketplace database seeding..."))

        with transaction.atomic():
            # 1. Admin Account
            admin_user, created = User.objects.get_or_create(
                username='admin',
                defaults={
                    'email': 'admin@freshmart.com',
                    'first_name': 'Chief',
                    'last_name': 'Administrator',
                    'role': User.Role.ADMIN,
                    'account_status': User.Status.ACTIVE,
                    'is_staff': True,
                    'is_superuser': True,
                }
            )
            admin_user.set_password('admin123')
            admin_user.save()

            # 2. Seller Accounts
            seller1, _ = User.objects.get_or_create(
                username='seller_green',
                defaults={
                    'email': 'greenvalley@freshmart.com',
                    'first_name': 'Aarav',
                    'last_name': 'Verma',
                    'role': User.Role.SELLER,
                    'phone_number': '+91 98111 22334',
                    'account_status': User.Status.ACTIVE,
                }
            )
            seller1.set_password('seller123')
            seller1.save()

            SellerProfile.objects.update_or_create(
                user=seller1,
                defaults={
                    'store_name': 'Green Valley Organics',
                    'store_slug': 'green-valley-organics',
                    'store_description': 'Certified organic farm vegetables, hydroponic greens, and crisp orchard fruits.',
                    'pickup_address': 'Plot 42, Sector 18 Agro Zone, Downtown',
                    'city': 'Central City',
                    'pin_code': '110001',
                    'is_approved': True,
                }
            )

            seller2, _ = User.objects.get_or_create(
                username='seller_dairy',
                defaults={
                    'email': 'puredairy@freshmart.com',
                    'first_name': 'Gurpreet',
                    'last_name': 'Singh',
                    'role': User.Role.SELLER,
                    'phone_number': '+91 98222 33445',
                    'account_status': User.Status.ACTIVE,
                }
            )
            seller2.set_password('seller123')
            seller2.save()

            SellerProfile.objects.update_or_create(
                user=seller2,
                defaults={
                    'store_name': 'Pure Dairy & Farm Foods',
                    'store_slug': 'pure-dairy-farm-foods',
                    'store_description': 'A2 milk, artisan paneer, cold-pressed oils, and farm fresh breakfast staples.',
                    'pickup_address': 'Dairy Farm Shed 7, Ring Road Bypass',
                    'city': 'Central City',
                    'pin_code': '110002',
                    'is_approved': True,
                }
            )

            # 3. Delivery Agent
            driver, _ = User.objects.get_or_create(
                username='delivery_agent',
                defaults={
                    'email': 'driver.rajesh@freshmart.com',
                    'first_name': 'Rajesh',
                    'last_name': 'Kumar',
                    'role': User.Role.DELIVERY,
                    'phone_number': '+91 98765 12345',
                    'account_status': User.Status.ACTIVE,
                }
            )
            driver.set_password('delivery123')
            driver.save()

            DeliveryProfile.objects.update_or_create(
                user=driver,
                defaults={
                    'vehicle_type': DeliveryProfile.VehicleType.BIKE,
                    'vehicle_number': 'DL 01 AB 8989',
                    'license_number': 'DL-2024-998877',
                    'current_status': DeliveryProfile.Status.AVAILABLE,
                    'total_deliveries_completed': 142,
                    'rating': Decimal('4.9'),
                }
            )

            # 4. Buyer Customer
            buyer, _ = User.objects.get_or_create(
                username='buyer_demo',
                defaults={
                    'email': 'pooja.sharma@gmail.com',
                    'first_name': 'Pooja',
                    'last_name': 'Sharma',
                    'role': User.Role.BUYER,
                    'phone_number': '+91 98999 88776',
                    'account_status': User.Status.ACTIVE,
                }
            )
            buyer.set_password('buyer123')
            buyer.save()

            BuyerProfile.objects.get_or_create(user=buyer)

            # Default Saved Address for Buyer
            Address.objects.update_or_create(
                user=buyer,
                full_name='Pooja Sharma',
                defaults={
                    'phone_number': '+91 98999 88776',
                    'house_number': 'Flat 304, Tower B',
                    'street': 'Sunrise Residency, Palm Grove Road',
                    'area': 'Downtown Sector 4',
                    'city': 'Central City',
                    'state': 'State',
                    'pin_code': '110001',
                    'landmark': 'Opposite City Center Mall',
                    'address_type': Address.AddressType.HOME,
                    'is_default': True,
                }
            )

            # 5. Categories
            category_defs = [
                ('Vegetables', 'fa-carrot', 'Farm picked organic greens, potatoes, root vegetables, and herbs.'),
                ('Fruits', 'fa-apple-whole', 'Seasonal, orchard-fresh apples, bananas, citrus, and berries.'),
                ('Dairy Products', 'fa-cheese', 'Fresh pasteurized milk, curd, organic paneer, and butter.'),
                ('Rice and Grains', 'fa-wheat-awn', 'Aromatic basmati rice, brown rice, whole wheat, and millets.'),
                ('Pulses', 'fa-seedling', 'Nutritious unpolished dals, lentils, rajma, and chickpeas.'),
                ('Cooking Oil', 'fa-bottle-droplet', 'Cold-pressed mustard oil, groundnut oil, pure cow ghee, and olive oil.'),
                ('Snacks', 'fa-cookie-bite', 'Healthy roasted dry fruits, crispy snacks, and nutritious bites.'),
                ('Beverages', 'fa-mug-hot', 'Fresh fruit juices, tender coconut water, organic tea, and coffee.'),
                ('Bakery', 'fa-bread-slice', 'Artisan sourdough, whole-wheat breads, and freshly baked muffins.'),
                ('Personal Care', 'fa-pump-soap', 'Herbal soaps, daily skincare, dental hygiene, and bath care.'),
                ('Household Products', 'fa-spray-can-sparkles', 'Eco-friendly kitchen cleaners, detergents, and paper towels.'),
            ]

            category_map = {}
            for sort_idx, (cat_name, icon, desc) in enumerate(category_defs):
                cat_slug = slugify(cat_name)
                cat, _ = Category.objects.update_or_create(
                    slug=cat_slug,
                    defaults={
                        'name': cat_name,
                        'icon': icon,
                        'description': desc,
                        'sort_order': sort_idx,
                        'is_active': True,
                    }
                )
                category_map[cat_name] = cat

            # 6. Sample Products
            sample_products = [
                {
                    'seller': seller1,
                    'category': category_map['Snacks'],
                    'name': "Lay's Classic Salted Potato Chips",
                    'brand': "Lay's",
                    'unit_value': '52 g packet',
                    'unit': 'packets',
                    'mrp': Decimal('25.00'),
                    'selling_price': Decimal('20.00'),
                    'stock': 100,
                    'low_stock': 20,
                    'origin': 'Frito-Lay Plant',
                    'freshness': 'Freshly packed batch',
                    'featured': True,
                    'bestseller': True,
                    'description': "Crispy, golden-sliced potato chips lightly sprinkled with salt. The ultimate snack."
                },
                {
                    'seller': seller1,
                    'category': category_map['Vegetables'],
                    'name': 'Fresh Farm Potatoes (Aloo)',
                    'brand': 'Green Valley',
                    'unit_value': '1 kg',
                    'unit': 'kg',
                    'mrp': Decimal('40.00'),
                    'selling_price': Decimal('30.00'),
                    'stock': 500,
                    'low_stock': 50,
                    'origin': 'Agra Farms',
                    'freshness': 'Direct from harvest',
                    'featured': True,
                    'bestseller': True,
                    'description': 'Firm, smooth-skinned cooking potatoes perfect for curries, boiling, and roasting.'
                },
                {
                    'seller': seller2,
                    'category': category_map['Rice and Grains'],
                    'name': 'Royal Aged Basmati Rice',
                    'brand': 'Shahi Heritage',
                    'unit_value': '1 kg',
                    'unit': 'kg',
                    'mrp': Decimal('160.00'),
                    'selling_price': Decimal('135.00'),
                    'stock': 150,
                    'low_stock': 25,
                    'origin': 'Punjab Tarai Region',
                    'freshness': 'Aged 2 years for fluffiness',
                    'featured': True,
                    'bestseller': True,
                    'description': 'Extra-long slender basmati pearls with exquisite aroma, perfect for biryanis and daily meals.'
                },
                {
                    'seller': seller2,
                    'category': category_map['Dairy Products'],
                    'name': 'Pure Farm Fresh Cow Milk',
                    'brand': 'Pure Dairy',
                    'unit_value': '1 Litre Pouch',
                    'unit': 'litres',
                    'mrp': Decimal('72.00'),
                    'selling_price': Decimal('66.00'),
                    'stock': 75,
                    'low_stock': 20,
                    'origin': 'Pure Dairy Farms',
                    'freshness': 'Pasteurized & cold chained',
                    'featured': True,
                    'bestseller': True,
                    'description': 'Nutritious farm fresh cow milk untouched by human hands and stringently tested for purity.'
                },
                {
                    'seller': seller2,
                    'category': category_map['Dairy Products'],
                    'name': 'Farm Fresh Country Brown Eggs',
                    'brand': 'Country Hen',
                    'unit_value': '6 pieces pack',
                    'unit': 'pieces',
                    'mrp': Decimal('65.00'),
                    'selling_price': Decimal('54.00'),
                    'stock': 25,
                    'low_stock': 10,
                    'origin': 'Free Range Organic Farms',
                    'freshness': 'Collected daily',
                    'featured': True,
                    'bestseller': True,
                    'description': 'Protein-rich farm brown eggs from healthy free-range hens.'
                },
                {
                    'seller': seller1,
                    'category': category_map['Fruits'],
                    'name': 'Kashmiri Royal Gala Apples',
                    'brand': 'Himalayan Orchards',
                    'unit_value': '1 kg',
                    'unit': 'kg',
                    'mrp': Decimal('240.00'),
                    'selling_price': Decimal('199.00'),
                    'stock': 0, # Out of stock demonstration!
                    'low_stock': 15,
                    'origin': 'Kashmir Valley',
                    'freshness': 'Harvest ended for current batch',
                    'featured': False,
                    'bestseller': False,
                    'description': 'Crisp, sweet, juicy Himalayan apples. Currently sold out until next seasonal batch.'
                },
                {
                    'seller': seller1,
                    'category': category_map['Vegetables'],
                    'name': 'Fresh Organic Hybrid Tomatoes',
                    'brand': 'Green Valley',
                    'unit_value': '1 kg',
                    'unit': 'kg',
                    'mrp': Decimal('45.00'),
                    'selling_price': Decimal('32.00'),
                    'stock': 85,
                    'low_stock': 15,
                    'origin': 'Local Greenhouse Farm',
                    'freshness': 'Picked at 5 AM today',
                    'featured': True,
                    'bestseller': True,
                    'description': 'Crisp, juicy vine-ripened tomatoes rich in lycopene and vitamin C, ideal for curries and fresh salads.'
                },
                {
                    'seller': seller1,
                    'category': category_map['Vegetables'],
                    'name': 'Farm Fresh Green Capsicum / Shimla Mirch',
                    'brand': 'Green Valley',
                    'unit_value': '500 g',
                    'unit': 'g',
                    'mrp': Decimal('40.00'),
                    'selling_price': Decimal('28.00'),
                    'stock': 40,
                    'low_stock': 10,
                    'origin': 'Hydroponic Valley',
                    'freshness': 'Harvested within 24 hours',
                    'featured': False,
                    'bestseller': False,
                    'description': 'Crunchy bell peppers packed with aroma and nutrients for pasta, stir-fries, and pizzas.'
                },
                {
                    'seller': seller1,
                    'category': category_map['Vegetables'],
                    'name': 'Organic Red Onions (Pyaz)',
                    'brand': 'Green Valley',
                    'unit_value': '2 kg pack',
                    'unit': 'kg',
                    'mrp': Decimal('90.00'),
                    'selling_price': Decimal('68.00'),
                    'stock': 120,
                    'low_stock': 20,
                    'origin': 'Nashik Organic Farms',
                    'freshness': 'Dry cured and hand sorted',
                    'featured': False,
                    'bestseller': True,
                    'description': 'Firm and pungent red onions essential for Indian gravies and everyday cooking.'
                },
                {
                    'seller': seller2,
                    'category': category_map['Cooking Oil'],
                    'name': 'Pure Cold-Pressed Kachi Ghani Mustard Oil',
                    'brand': 'Kisan Gold',
                    'unit_value': '1 Litre Bottle',
                    'unit': 'litres',
                    'mrp': Decimal('185.00'),
                    'selling_price': Decimal('149.00'),
                    'stock': 45,
                    'low_stock': 8,
                    'origin': 'Rajasthan Mills',
                    'freshness': 'Traditional wood expeller pressed',
                    'featured': False,
                    'bestseller': False,
                    'description': 'Unrefined, pungent cold pressed mustard oil rich in natural antioxidants and omega-3.'
                },
                {
                    'seller': seller1,
                    'category': category_map['Vegetables'],
                    'name': 'Hydroponic English Cucumbers',
                    'brand': 'Green Valley',
                    'unit_value': '500 g',
                    'unit': 'g',
                    'mrp': Decimal('45.00'),
                    'selling_price': Decimal('32.00'),
                    'stock': 4, # Low stock demo!
                    'low_stock': 5,
                    'origin': 'Downtown Greenhouse',
                    'freshness': 'Crisp and seedless',
                    'featured': False,
                    'bestseller': False,
                    'description': 'Thin-skinned, seedless, and refreshing cucumbers ideal for healthy detox juices and salads.'
                },
            ]

            for p_idx, p_data in enumerate(sample_products, start=1):
                prod_slug = slugify(f"{p_data['name']}-{p_data['seller'].username}")
                sku = f"SKU-{p_data['seller'].id}-{slugify(p_data['name'])[:8].upper()}-{p_idx:03d}"
                
                product, _ = Product.objects.update_or_create(
                    slug=prod_slug,
                    defaults={
                        'seller': p_data['seller'],
                        'category': p_data['category'],
                        'name': p_data['name'],
                        'brand': p_data['brand'],
                        'unit_value': p_data['unit_value'],
                        'unit': p_data['unit'],
                        'mrp': p_data['mrp'],
                        'selling_price': p_data['selling_price'],
                        'sku': sku,
                        'origin': p_data['origin'],
                        'freshness_info': p_data['freshness'],
                        'description': p_data['description'],
                        'is_featured': p_data['featured'],
                        'is_best_seller': p_data['bestseller'],
                        'is_active': True,
                    }
                )

                # Inventory
                StockRecord.objects.update_or_create(
                    product=product,
                    defaults={
                        'current_stock': p_data['stock'],
                        'low_stock_threshold': p_data['low_stock'],
                    }
                )

            # 7. Demo Sample Order for end-to-end testing across all 4 roles!
            demo_order_id = "ORD-20261014-DEMO1"
            order, o_created = Order.objects.get_or_create(
                order_id=demo_order_id,
                defaults={
                    'buyer': buyer,
                    'recipient_name': 'Pooja Sharma',
                    'recipient_phone': '+91 98999 88776',
                    'delivery_address_text': 'Flat 304, Tower B, Sunrise Residency, Palm Grove Road, Downtown Sector 4, Central City - 110001',
                    'pin_code': '110001',
                    'delivery_slot': '10:00 AM - 01:00 PM',
                    'status': Order.Status.PACKED,
                    'payment_method': Order.PaymentMethod.COD,
                    'payment_status': Order.PaymentStatus.PENDING,
                    'subtotal': Decimal('277.00'),
                    'delivery_fee': Decimal('0.00'),
                    'total_amount': Decimal('277.00'),
                }
            )

            if o_created:
                # Add status history
                order.add_status_history(Order.Status.ORDER_PLACED, buyer, "Order placed successfully.")
                order.add_status_history(Order.Status.ORDER_CONFIRMED, seller1, "Order confirmed by vendor.")
                order.add_status_history(Order.Status.PACKED, seller1, "All vegetables sealed in eco bags.")

                # Sub orders
                sub1 = SellerSubOrder.objects.create(
                    order=order,
                    seller=seller1,
                    subtotal=Decimal('277.00'),
                    status=SellerSubOrder.Status.PACKED
                )

                # Order Items for demo order
                first_prod = Product.objects.filter(seller=seller1).first()
                if first_prod:
                    OrderItem.objects.create(
                        order=order,
                        sub_order=sub1,
                        seller=seller1,
                        product=first_prod,
                        product_name=first_prod.name,
                        brand=first_prod.brand,
                        unit_value=first_prod.unit_value,
                        unit_price=first_prod.selling_price,
                        mrp=first_prod.mrp,
                        quantity=2,
                        total_price=first_prod.selling_price * 2,
                        image_url=first_prod.primary_image_url
                    )

                # Assign to delivery driver
                DeliveryAssignment.objects.create(
                    order=order,
                    delivery_person=driver,
                    assigned_by=admin_user,
                    status=DeliveryAssignment.Status.ASSIGNED
                )

            # 8. Delivered / Completed Order for Seller Order History Demonstration
            delivered_order_id = "ORD-20261015-DELIV1"
            deliv_order, d_created = Order.objects.get_or_create(
                order_id=delivered_order_id,
                defaults={
                    'buyer': buyer,
                    'recipient_name': 'Pooja Sharma',
                    'recipient_phone': '+91 98999 88776',
                    'delivery_address_text': 'Flat 304, Tower B, Sunrise Residency, Palm Grove Road, Downtown Sector 4, Central City - 110001',
                    'pin_code': '110001',
                    'delivery_slot': '07:00 AM - 10:00 AM',
                    'status': Order.Status.DELIVERED,
                    'payment_method': Order.PaymentMethod.COD,
                    'payment_status': Order.PaymentStatus.PAID,
                    'subtotal': Decimal('132.00'),
                    'delivery_fee': Decimal('0.00'),
                    'total_amount': Decimal('132.00'),
                }
            )

            if d_created:
                deliv_order.add_status_history(Order.Status.ORDER_PLACED, buyer, "Order placed successfully.")
                deliv_order.add_status_history(Order.Status.ORDER_CONFIRMED, seller1, "Order confirmed by vendor.")
                deliv_order.add_status_history(Order.Status.PACKED, seller1, "All vegetables sealed in eco bags.")
                deliv_order.add_status_history(Order.Status.DELIVERED, driver, "Delivered to customer doorstep. Verified via Customer OTP.")

                sub_deliv = SellerSubOrder.objects.create(
                    order=deliv_order,
                    seller=seller1,
                    subtotal=Decimal('132.00'),
                    status=SellerSubOrder.Status.DELIVERED
                )

                seller1_prods = list(Product.objects.filter(seller=seller1)[:2])
                if seller1_prods:
                    for p in seller1_prods:
                        OrderItem.objects.create(
                            order=deliv_order,
                            sub_order=sub_deliv,
                            seller=seller1,
                            product=p,
                            product_name=p.name,
                            brand=p.brand,
                            unit_value=p.unit_value,
                            unit_price=p.selling_price,
                            mrp=p.mrp,
                            quantity=2,
                            total_price=p.selling_price * 2,
                            image_url=p.primary_image_url
                        )

                DeliveryAssignment.objects.create(
                    order=deliv_order,
                    delivery_person=driver,
                    assigned_by=admin_user,
                    status=DeliveryAssignment.Status.DELIVERED,
                    delivered_time=timezone.now() - timezone.timedelta(hours=3),
                    cod_collected=True,
                    cod_collected_amount=Decimal('132.00'),
                    shop_verified=True
                )

        self.stdout.write(self.style.SUCCESS("""
========================================================================
FreshMart Marketplace Seed Data Created Successfully!
========================================================================
Logins Available for All 4 Roles:
1. Administrator:
   - Username: admin
   - Password: admin123
   - Portal:   http://127.0.0.1:8000/admin-dashboard/

2. Seller / Vendor (Vegetables & Produce):
   - Username: seller_green
   - Password: seller123
   - Store:    Green Valley Organics
   - Portal:   http://127.0.0.1:8000/seller/dashboard/

3. Seller / Vendor (Dairy & Grains):
   - Username: seller_dairy
   - Password: seller123
   - Store:    Pure Dairy & Farm Foods

4. Delivery Fleet Agent:
   - Username: delivery_agent
   - Password: delivery123
   - Portal:   http://127.0.0.1:8000/delivery/dashboard/

5. Buyer / Customer:
   - Username: buyer_demo
   - Password: buyer123
   - Portal:   http://127.0.0.1:8000/
========================================================================
"""))
