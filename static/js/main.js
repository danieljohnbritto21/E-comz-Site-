/* ==========================================================================
   GROCERY MARKETPLACE JAVASCRIPT
   Modern Fetch API helpers, cart handling, live search, and UX utilities
   ========================================================================== */

window.formatINR = function(val, includeSymbol = true) {
  if (val === null || val === undefined || val === '') return includeSymbol ? '₹0.00' : '0.00';
  let cleanStr = String(val).replace(/[^0-9.-]/g, '');
  let num = parseFloat(cleanStr);
  if (isNaN(num)) return includeSymbol ? '₹0.00' : '0.00';
  let formatted = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(num);
  return includeSymbol ? formatted : formatted.replace(/[^\d.,-]/g, '').trim();
};

function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + '=')) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  if (!cookieValue && name === 'csrftoken') {
    const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (csrfInput) cookieValue = csrfInput.value;
  }
  return cookieValue;
}

function getCsrfToken() {
  return getCookie('csrftoken') || '';
}

// Toast Notification System with Optional Link
function showToast(message, type = 'success', actionUrl = null, actionText = 'View Basket') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = `
      position: fixed;
      bottom: 90px;
      right: 24px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 10px;
    `;
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  const bgColors = {
    success: '#059669',
    danger: '#DC2626',
    warning: '#D97706',
    info: '#2563EB'
  };

  toast.style.cssText = `
    background: ${bgColors[type] || '#111827'};
    color: #ffffff;
    padding: 12px 18px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    box-shadow: 0 10px 25px -3px rgba(0,0,0,0.25);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    opacity: 0;
    transform: translateY(10px);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  `;

  let actionHtml = '';
  if (actionUrl) {
    actionHtml = `<a href="${actionUrl}" style="color: #FEF08A; font-weight: 800; text-decoration: underline; white-space: nowrap; font-size: 13px;">${actionText} &rarr;</a>`;
  }

  toast.innerHTML = `<div style="display: flex; align-items: center; gap: 8px;"><i class="fa-solid fa-circle-check"></i><span>${message}</span></div>${actionHtml}`;
  container.appendChild(toast);

  // Trigger animation
  setTimeout(() => {
    toast.style.opacity = '1';
    toast.style.transform = 'translateY(0)';
  }, 10);

  // Auto remove after 3.5s
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Master UI Synchronizer for Cart
function updateCartUI(data) {
  if (!data) return;

  // 1. Update product card in-place stepper
  if (data.product_id) {
    const wrap = document.getElementById(`cart-wrap-${data.product_id}`);
    const addBtn = document.getElementById(`add-btn-${data.product_id}`);
    const stepper = document.getElementById(`stepper-${data.product_id}`);
    const qtyVal = document.getElementById(`qty-val-${data.product_id}`);

    if (data.available_stock !== undefined && data.available_stock <= 0) {
      if (wrap) {
        wrap.innerHTML = `<button type="button" class="btn-out-of-stock" id="add-btn-${data.product_id}" disabled aria-disabled="true"><i class="fa-solid fa-circle-xmark"></i> Out of Stock</button>`;
      }
    } else if (data.item_quantity > 0) {
      if (addBtn) addBtn.classList.add('is-hidden');
      if (stepper) stepper.classList.remove('is-hidden');
      if (qtyVal) qtyVal.textContent = data.item_quantity;
    } else {
      if (addBtn) addBtn.classList.remove('is-hidden');
      if (stepper) stepper.classList.add('is-hidden');
      if (qtyVal) qtyVal.textContent = '0';
    }
  }

  // 2. Update Floating Bottom Cart Dock
  const floatBar = document.getElementById('floating-cart-bar');
  const floatBadge = document.getElementById('float-badge');
  const floatCount = document.getElementById('float-cart-count');
  const floatUnit = document.getElementById('float-cart-unit');
  const floatSubtotal = document.getElementById('float-cart-subtotal');

  if (floatBadge) floatBadge.textContent = data.total_items;
  if (floatCount) floatCount.textContent = data.total_items;
  if (floatUnit) floatUnit.textContent = data.total_items === 1 ? 'Item' : 'Items';
  if (floatSubtotal) floatSubtotal.textContent = data.subtotal;

  if (floatBar) {
    if (data.total_items > 0) {
      floatBar.classList.add('is-visible');
    } else {
      floatBar.classList.remove('is-visible');
    }
  }

  // 3. Update top navbar header badges with pulse effect
  const cartBadges = document.querySelectorAll('.cart-count-badge');
  cartBadges.forEach(b => {
    b.textContent = data.total_items;
    b.classList.remove('anim-pulse');
    void b.offsetWidth; // trigger reflow
    b.classList.add('anim-pulse');
  });
  const cartPrices = document.querySelectorAll('.nav-cart-price');
  cartPrices.forEach(p => {
    p.textContent = data.subtotal;
  });
}

// Handle In-Place Product Card Action (+Add, [+] increment, [-] decrement)
async function handleCartAction(productId, delta, maxStock = null, unit = 'units', actualStock = null, orderLimit = null) {
  // If user is logged in with a non-BUYER role (SELLER, DELIVERY, ADMIN), restrict
  if (window.FRESHMART_USER && window.FRESHMART_USER.isAuthenticated && window.FRESHMART_USER.role !== 'BUYER') {
    showToast('Only customer accounts can add products to cart and place orders.', 'warning');
    return;
  }

  // Client-side quick stock & order limit check
  const wrap = document.getElementById(`cart-wrap-${productId}`);
  if (wrap) {
    if (maxStock === null && wrap.dataset.actualMax) {
      maxStock = parseInt(wrap.dataset.actualMax, 10);
    } else if (maxStock === null && wrap.dataset.maxStock) {
      maxStock = parseInt(wrap.dataset.maxStock, 10);
    }
    if (actualStock === null && wrap.dataset.maxStock) {
      actualStock = parseInt(wrap.dataset.maxStock, 10);
    }
    if (orderLimit === null && wrap.dataset.orderLimit) {
      orderLimit = parseInt(wrap.dataset.orderLimit, 10);
    }
    unit = wrap.dataset.unit || unit;
  }
  const qtyVal = document.getElementById(`qty-val-${productId}`);
  const currentQty = qtyVal ? parseInt(qtyVal.textContent.trim() || '0', 10) : 0;

  if (delta > 0 && maxStock !== null && !isNaN(maxStock)) {
    if (maxStock <= 0) {
      showToast('This product is currently out of stock.', 'danger');
      return;
    }
    if (currentQty + delta > maxStock) {
      if (actualStock !== null && orderLimit !== null && actualStock < orderLimit) {
        showToast(`Cannot add more. Only ${actualStock} ${unit} available in stock.`, 'warning');
      } else if (orderLimit !== null) {
        showToast(`You can order a maximum of ${orderLimit} ${unit} per order.`, 'warning');
      } else {
        showToast(`Cannot add more. Maximum limit is ${maxStock} ${unit}.`, 'warning');
      }
      return;
    }
  }

  try {
    const response = await fetch('/cart/add/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ product_id: productId, delta: delta })
    });

    const data = await response.json();
    if (response.ok && data.success) {
      updateCartUI(data);
      if (delta > 0) {
        showToast(data.message || 'Item added to basket!', 'success', '/cart/', 'View Basket');
      } else if (data.item_quantity === 0) {
        showToast('Item removed from basket', 'info');
      }
    } else if (response.status === 401 && data.require_login) {
      window.location.href = data.login_url || '/auth/login/?role=BUYER';
    } else {
      // If server indicates product is out of stock, reflect in UI immediately
      if (data.available_stock !== undefined && data.available_stock <= 0) {
        if (wrap) {
          wrap.innerHTML = `<button type="button" class="btn-out-of-stock" id="add-btn-${productId}" disabled aria-disabled="true"><i class="fa-solid fa-circle-xmark"></i> Out of Stock</button>`;
        }
      }
      showToast(data.error || 'Could not update basket', 'danger');
    }
  } catch (err) {
    console.error('Cart action error:', err);
    showToast('Network error while updating cart', 'danger');
  }
}

// Add to Cart (Standard / Detail page)
async function addToCart(productId, quantity = 1, maxStock = null, unit = 'units') {
  // If user is logged in with a non-BUYER role (SELLER, DELIVERY, ADMIN), restrict
  if (window.FRESHMART_USER && window.FRESHMART_USER.isAuthenticated && window.FRESHMART_USER.role !== 'BUYER') {
    showToast('Only customer accounts can add products to cart and place orders.', 'warning');
    return;
  }

  if (maxStock !== null && !isNaN(maxStock)) {
    if (maxStock <= 0) {
      showToast('This product is currently out of stock.', 'danger');
      return;
    }
    if (quantity > maxStock) {
      showToast(`Cannot add more than permitted maximum (${maxStock} ${unit}).`, 'warning');
      return;
    }
  }

  try {
    const response = await fetch('/cart/add/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ product_id: productId, quantity: quantity })
    });

    const data = await response.json();
    if (response.ok && data.success) {
      updateCartUI(data);
      showToast(data.message || 'Added to cart!', 'success', '/cart/', 'View Basket');
    } else if (response.status === 401 && data.require_login) {
      window.location.href = data.login_url || '/auth/login/?role=BUYER';
    } else {
      if (data.available_stock !== undefined && data.available_stock <= 0) {
        const detailBtn = document.getElementById('add-to-cart-btn');
        if (detailBtn) {
          detailBtn.disabled = true;
          detailBtn.innerHTML = '<i class="fa-solid fa-circle-xmark"></i> Out of Stock';
        }
      }
      showToast(data.error || 'Failed to add item', 'danger');
    }
  } catch (err) {
    console.error('Add to cart error:', err);
    showToast('Network error while adding to cart', 'danger');
  }
}

// Update Cart Quantity
async function updateCartItem(itemId, newQty) {
  if (newQty < 1) {
    return removeCartItem(itemId);
  }
  try {
    const response = await fetch(`/cart/update/${itemId}/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ quantity: newQty })
    });

    const data = await response.json();
    if (response.ok && data.success) {
      window.location.reload();
    } else {
      showToast(data.error || 'Could not update quantity', 'danger');
    }
  } catch (err) {
    console.error('Update cart error:', err);
  }
}

// Remove from Cart
async function removeCartItem(itemId) {
  if (!confirm('Remove this item from your cart?')) return;
  try {
    const response = await fetch(`/cart/remove/${itemId}/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      }
    });

    const data = await response.json();
    if (response.ok && data.success) {
      window.location.reload();
    } else {
      showToast(data.error || 'Could not remove item', 'danger');
    }
  } catch (err) {
    console.error('Remove cart error:', err);
  }
}

// Toggle Wishlist
async function toggleWishlist(productId, btnElement) {
  if (window.FRESHMART_USER && !window.FRESHMART_USER.isAuthenticated) {
    const nextUrl = window.location.pathname + window.location.search;
    const baseLogin = window.FRESHMART_USER.loginUrl || '/auth/login/';
    window.location.href = `${baseLogin}?role=BUYER&next=${encodeURIComponent(nextUrl)}`;
    return;
  }

  try {
    const response = await fetch(`/buyer/wishlist/toggle/${productId}/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      }
    });

    const data = await response.json();
    if (response.ok && data.success) {
      if (btnElement) {
        btnElement.classList.toggle('active', data.in_wishlist);
      }
      showToast(data.message, 'success');
    } else {
      showToast(data.error || 'Please log in to add items to wishlist', 'warning');
    }
  } catch (err) {
    console.error('Wishlist error:', err);
  }
}

// Live Search Product Suggestions
document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('main-search-input');
  const suggestionsBox = document.getElementById('search-suggestions');

  if (searchInput && suggestionsBox) {
    let debounceTimer;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      const query = e.target.value.trim();
      if (query.length < 2) {
        suggestionsBox.style.display = 'none';
        return;
      }

      debounceTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/search-suggest/?q=${encodeURIComponent(query)}`);
          const data = await res.json();
          if (data.results && data.results.length > 0) {
            suggestionsBox.innerHTML = data.results.map(item => `
              <a href="/product/${item.slug}/" style="display: flex; align-items: center; gap: 12px; padding: 10px 14px; text-decoration: none; color: #111827; border-bottom: 1px solid #f3f4f6;">
                <img src="${item.image}" style="width: 36px; height: 36px; object-fit: contain; border-radius: 4px;">
                <div>
                  <div style="font-weight: 600; font-size: 13px;">${item.name}</div>
                  <div style="font-size: 12px; color: #059669; font-weight: 700;">${window.formatINR(item.selling_price)}</div>
                </div>
              </a>
            `).join('');
            suggestionsBox.style.display = 'block';
          } else {
            suggestionsBox.style.display = 'none';
          }
        } catch (err) {
          console.error('Suggestion fetch error', err);
        }
      }, 300);
    });

    document.addEventListener('click', (e) => {
      if (!searchInput.contains(e.target) && !suggestionsBox.contains(e.target)) {
        suggestionsBox.style.display = 'none';
      }
    });
  }
});

/* ==========================================================================
   MOBILE RESPONSIVE CONTROLS
   ========================================================================== */
function openMobileDrawer() {
  const drawer = document.getElementById('mobile-nav-drawer');
  const overlay = document.getElementById('mobile-drawer-overlay');
  if (drawer) drawer.classList.add('is-open');
  if (overlay) overlay.classList.add('is-open');
  document.body.style.overflow = 'hidden';
}

function closeMobileDrawer() {
  const drawer = document.getElementById('mobile-nav-drawer');
  const overlay = document.getElementById('mobile-drawer-overlay');
  if (drawer) drawer.classList.remove('is-open');
  if (overlay) overlay.classList.remove('is-open');
  document.body.style.overflow = '';
}

function focusMainSearch() {
  const input = document.getElementById('main-search-input');
  if (input) {
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => input.focus(), 250);
  }
}

function toggleDashboardSidebar() {
  const sidebar = document.querySelector('.dashboard-sidebar');
  if (sidebar) {
    sidebar.classList.toggle('menu-open');
  }
}

function toggleShopFilters() {
  const sidebar = document.getElementById('shop-filters-sidebar');
  const overlay = document.getElementById('shop-filters-overlay');
  if (sidebar) sidebar.classList.toggle('is-open');
  if (overlay) overlay.classList.toggle('is-open');
}
