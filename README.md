<<<<<<< HEAD
# FreshMart - Multi-Role Grocery E-Commerce Platform

A multi-role grocery marketplace web application built with **HTML5, CSS3, Vanilla JavaScript, Django, and PostgreSQL-readiness**. Features a modern, high-contrast, responsive white-and-emerald-green user interface with real frontend-to-backend database synchronization across all four user roles.

---

## 🌟 The 4 User Roles & Connected Workflows

All user roles operate on a unified backend database with role-based access control and live notification broadcasts:

| Role | Primary Dashboard | Key Responsibilities & Capabilities |
| :--- | :--- | :--- |
| **Buyer / Customer** | `/` & `/shop/` | Browse catalog, live search with suggestions, add to cart, choose delivery slots, checkout (COD / online), live milestone tracking, reviews, wishlist. |
| **Seller / Vendor** | `/seller/dashboard/` | Add/edit grocery products with multiple images, manage stock levels, receive vendor-specific suborders, accept, pack, and mark ready for pickup. |
| **Delivery Person** | `/delivery/dashboard/` | View assigned orders, inspect pickup store & customer destinations, mark Picked Up, mark Out for Delivery, collect COD, mark Delivered or record failure reasons. |
| **Admin** | `/admin-dashboard/` | Platform KPI metrics, approve/suspend sellers, provision delivery agents, manage categories, assign delivery drivers to orders, export audited sales reports to CSV. |

---

## 🚀 Quick Start Guide

### 1. Requirements
- Python 3.10+
- Django 5.x / 6.x
- SQLite (pre-configured for instant zero-setup local development) or PostgreSQL

### 2. Setup & Database Initialization
```bash
# Clone or navigate to the project directory
cd "d:/E-com Project"

# Install dependencies
pip install -r requirements.txt

# Run migrations to generate the database schema
python manage.py migrate

# Seed complete demo data (roles, categories, produce, stock, sample orders)
python manage.py seed_data

# Start the local development server
python manage.py runserver
```

Open your browser at **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**.

---

## 🔑 Pre-Configured Demo Credentials

The `seed_data` command creates ready-to-test accounts for all four user roles (you can also autofill them directly from the login page):

| Role | Username | Password | Dashboard URL | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Admin** | `admin` | `admin123` | `http://127.0.0.1:8000/admin-dashboard/` | Full platform control, driver assignment, seller approval |
| **Seller 1** | `seller_green` | `seller123` | `http://127.0.0.1:8000/seller/dashboard/` | "Green Valley Organics" (Fresh Vegetables & Fruits) |
| **Seller 2** | `seller_dairy` | `seller123` | `http://127.0.0.1:8000/seller/dashboard/` | "Pure Dairy & Farm Foods" (A2 Milk, Paneer, Oil) |
| **Delivery Agent** | `delivery_agent` | `delivery123` | `http://127.0.0.1:8000/delivery/dashboard/` | "Rajesh Kumar" (Motorcycle Fleet Agent) |
| **Buyer** | `buyer_demo` | `buyer123` | `http://127.0.0.1:8000/` | "Pooja Sharma" (Customer with saved address & history) |

---

## ⚙️ Switching from SQLite to PostgreSQL

The project is architected with environment variable-driven database switching. To connect to PostgreSQL:

1. Open your `.env` file (copied from `.env.example`).
2. Update the database configuration settings:
   ```env
   USE_POSTGRES=True
   DB_NAME=grocery_db
   DB_USER=postgres
   DB_PASSWORD=your_postgres_password
   DB_HOST=localhost
   DB_PORT=5432
   ```
3. Run `python manage.py migrate` and `python manage.py seed_data`.

---

## 🛒 Complete End-to-End Workflow Demonstration

1. **Product Creation (Seller &rarr; Buyer):**
   - Log in as `seller_green` at `/seller/dashboard/`.
   - Go to **Add New Product**, upload photos, set MRP, selling price, unit, and stock.
   - The product instantly appears on the buyer's storefront at `/shop/`!

2. **Order Placement (Buyer &rarr; Multi-Vendor):**
   - Log in as `buyer_demo` at `/auth/login/`.
   - Add items to your basket and proceed to **Checkout** (`/orders/checkout/`).
   - Select your delivery slot and payment method (e.g., COD).
   - The system atomically reserves and deducts stock, generating sub-orders for each participating seller.

3. **Packaging & Preparation (Seller &rarr; Dispatch):**
   - Log in as `seller_green` at `/seller/orders/`.
   - Open the order, click **Accept Order**, then **Start Preparing Produce**, and finally **Mark Ready for Delivery Pickup**.

4. **Dispatch & Handover (Admin &rarr; Delivery):**
   - Log in as `admin` at `/admin-dashboard/orders/`.
   - Select `delivery_agent` from the dropdown and click **Assign**.
   - Log in as `delivery_agent` at `/delivery/dashboard/`. The task appears under **Active Tasks**.
   - Driver marks **Picked Up from Store**, then **Out for Delivery**, and finally **Complete & Mark Delivered** with COD collection recording.

5. **Live Tracking & Audit (Buyer & Admin):**
   - At `/orders/track/<order_id>/`, the buyer sees the updated 7-stage milestone stepper and the driver's contact details.
   - At `/admin-dashboard/reports/`, platform administrators can view financial summaries and download the complete **CSV Sales Report**.

---

## 🧪 Running Automated Tests

Run the test suite covering role access, inventory safety, checkout, delivery, and reports:
```bash
python manage.py test
```
Result: **7 passed (0 failures, 0 errors)**.
=======
# E-comz-Site-
A Django-based single-store grocery e-commerce platform with customer shopping, product management, inventory control, and seller dashboard.
>>>>>>> 02a8cae52c2ce94f32f9213c1e298b374feb205a
