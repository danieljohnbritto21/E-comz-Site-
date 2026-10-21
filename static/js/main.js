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

// =============================================================================
// Modern Fresh Mart Toast Notification Manager
// Prevents stacking, deduplicates rapid identical calls, replaces active toasts
// =============================================================================
const ToastManager = {
  container: null,
  activeToast: null,
  dismissTimeout: null,
  lastMessage: null,
  lastType: null,
  lastShownTime: 0,
  minDedupeIntervalMs: 1600, // deduplicate repeated identical messages within 1.6s

  initContainer() {
    if (this.container && document.body.contains(this.container)) {
      return this.container;
    }
    let el = document.getElementById('toast-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast-container';
      el.className = 'toast-container';
      el.setAttribute('role', 'region');
      el.setAttribute('aria-label', 'Notifications');
      document.body.appendChild(el);
    } else {
      el.className = 'toast-container';
    }
    this.container = el;
    return el;
  },

  dismiss(toastElement = null, immediate = false) {
    const target = toastElement || this.activeToast;
    if (!target) return;

    if (this.dismissTimeout) {
      clearTimeout(this.dismissTimeout);
      this.dismissTimeout = null;
    }

    if (target === this.activeToast) {
      this.activeToast = null;
    }

    if (immediate) {
      if (target.parentNode) target.parentNode.removeChild(target);
      return;
    }

    target.classList.remove('toast-visible');
    target.classList.add('toast-hiding');
    setTimeout(() => {
      if (target.parentNode) {
        target.parentNode.removeChild(target);
      }
    }, 240);
  },

  show(message, type = 'success', actionUrl = null, actionText = 'View Basket') {
    if (!message) return;

    // Sanitize message: never display raw Django errors, HTML traces or overly long messages
    if (typeof message === 'string') {
      if (message.includes('<!DOCTYPE') || message.includes('Traceback') || message.includes('<html') || message.length > 250) {
        message = 'Unable to complete request. Please try again.';
      }
    } else if (typeof message === 'object') {
      message = message.error || message.message || 'An error occurred. Please try again.';
    }

    const normalizedType = ['success', 'warning', 'danger', 'info'].includes(type) ? type : (type === 'error' ? 'danger' : 'info');
    const now = Date.now();

    // 1. Deduplication: Same message & type within window -> nudge & refresh timer without re-creating DOM
    if (this.activeToast && 
        this.lastMessage === message && 
        this.lastType === normalizedType && 
        (now - this.lastShownTime < this.minDedupeIntervalMs)) {
      this.lastShownTime = now;
      this.activeToast.classList.remove('toast-nudge');
      void this.activeToast.offsetWidth; // trigger reflow for css animation
      this.activeToast.classList.add('toast-nudge');
      this.scheduleDismiss(normalizedType);
      return;
    }

    this.lastMessage = message;
    this.lastType = normalizedType;
    this.lastShownTime = now;

    const container = this.initContainer();

    // 2. Single toast policy: If an active toast exists, dismiss it immediately to avoid vertical stacking
    if (this.activeToast) {
      this.dismiss(this.activeToast, true);
    }
    // Clean up any stray child elements
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }

    // 3. Create fresh toast
    const toast = document.createElement('div');
    toast.className = `freshmart-toast toast-${normalizedType}`;
    
    // Accessibility attributes
    if (normalizedType === 'warning' || normalizedType === 'danger') {
      toast.setAttribute('role', 'alert');
      toast.setAttribute('aria-live', 'assertive');
    } else {
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
    }

    // Icon mapping
    const icons = {
      success: 'fa-circle-check',
      warning: 'fa-triangle-exclamation',
      danger: 'fa-circle-xmark',
      info: 'fa-circle-info'
    };
    const iconClass = icons[normalizedType] || 'fa-circle-info';

    // Action button HTML (e.g. View basket)
    let actionHtml = '';
    if (actionUrl) {
      actionHtml = `
        <a href="${actionUrl}" class="toast-action-btn" aria-label="${actionText}">
          <span>${actionText}</span>
          <i class="fa-solid fa-arrow-right" style="font-size: 0.7rem;"></i>
        </a>
      `;
    }

    toast.innerHTML = `
      <div class="toast-body">
        <div class="toast-icon" aria-hidden="true">
          <i class="fa-solid ${iconClass}"></i>
        </div>
        <div class="toast-message">${message}</div>
      </div>
      <div class="toast-actions">
        ${actionHtml}
        <button type="button" class="toast-close-btn" aria-label="Dismiss notification">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    `;

    // Hook up dismiss button
    const closeBtn = toast.querySelector('.toast-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.dismiss(toast);
      });
    }

    container.appendChild(toast);
    this.activeToast = toast;

    // Trigger enter animation
    requestAnimationFrame(() => {
      toast.classList.add('toast-visible');
    });

    // Auto-dismiss schedule
    this.scheduleDismiss(normalizedType);
  },

  scheduleDismiss(type) {
    if (this.dismissTimeout) {
      clearTimeout(this.dismissTimeout);
    }
    // Durations: success 2800ms, warning 3400ms, danger/error 4000ms, info 3000ms
    const durations = {
      success: 2800,
      warning: 3400,
      danger: 4000,
      info: 3000
    };
    const timeoutMs = durations[type] || 3000;
    this.dismissTimeout = setTimeout(() => {
      this.dismiss();
    }, timeoutMs);
  }
};

// Global showToast adapter for backward compatibility with entire codebase
function showToast(message, type = 'success', actionUrl = null, actionText = 'View Basket') {
  ToastManager.show(message, type, actionUrl, actionText);
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
      if (addBtn) {
        addBtn.classList.add('is-hidden');
        addBtn.style.display = 'none';
      }
      if (stepper) {
        stepper.classList.remove('is-hidden');
        stepper.style.display = 'flex';
      }
      if (qtyVal) qtyVal.textContent = data.item_quantity;
    } else {
      if (addBtn) {
        addBtn.classList.remove('is-hidden');
        addBtn.style.display = 'flex';
      }
      if (stepper) {
        stepper.classList.add('is-hidden');
        stepper.style.display = 'none';
      }
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

// In-flight request protection to prevent race conditions & duplicate notifications from rapid clicks
const _cartRequestsInFlight = new Set();

// Handle In-Place Product Card Action (+Add, [+] increment, [-] decrement)
async function handleCartAction(productId, delta, maxStock = null, unit = 'units', actualStock = null, orderLimit = null) {
  // If user is logged in with a non-BUYER role (SELLER, DELIVERY, ADMIN), restrict
  if (window.FRESHMART_USER && window.FRESHMART_USER.isAuthenticated && window.FRESHMART_USER.role !== 'BUYER') {
    showToast('Only customer accounts can add products to basket and place orders.', 'warning');
    return;
  }

  // Prevent duplicate concurrent requests while an operation is already in-flight
  if (_cartRequestsInFlight.has(productId)) {
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
      showToast('Sorry, this product is currently out of stock.', 'danger');
      return;
    }
    if (currentQty + delta > maxStock) {
      if (actualStock !== null && orderLimit !== null && actualStock < orderLimit) {
        showToast(`Cannot add more. Only ${actualStock} ${unit} available in stock.`, 'warning');
      } else if (orderLimit !== null) {
        showToast(`Maximum ${orderLimit} ${unit} per order for bike delivery.`, 'warning');
      } else {
        showToast(`Cannot add more. Maximum limit is ${maxStock} ${unit}.`, 'warning');
      }
      return;
    }
  }

  _cartRequestsInFlight.add(productId);

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
        showToast(data.message || 'Added to basket!', 'success', '/cart/', 'View basket');
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
    showToast('Network error while updating basket. Please try again.', 'danger');
  } finally {
    _cartRequestsInFlight.delete(productId);
  }
}

// Add to Cart (Standard / Detail page)
async function addToCart(productId, quantity = 1, maxStock = null, unit = 'units') {
  // If user is logged in with a non-BUYER role (SELLER, DELIVERY, ADMIN), restrict
  if (window.FRESHMART_USER && window.FRESHMART_USER.isAuthenticated && window.FRESHMART_USER.role !== 'BUYER') {
    showToast('Only customer accounts can add products to basket and place orders.', 'warning');
    return;
  }

  if (_cartRequestsInFlight.has(productId)) {
    return;
  }

  if (maxStock !== null && !isNaN(maxStock)) {
    if (maxStock <= 0) {
      showToast('Sorry, this product is currently out of stock.', 'danger');
      return;
    }
    if (quantity > maxStock) {
      showToast(`Cannot add more than permitted maximum (${maxStock} ${unit}).`, 'warning');
      return;
    }
  }

  _cartRequestsInFlight.add(productId);

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
      showToast(data.message || 'Added to basket!', 'success', '/cart/', 'View basket');
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
      showToast(data.error || 'Failed to add item to basket', 'danger');
    }
  } catch (err) {
    console.error('Add to cart error:', err);
    showToast('Network error while adding to basket. Please try again.', 'danger');
  } finally {
    _cartRequestsInFlight.delete(productId);
  }
}

// -------------------------------------------------------------
// Cart Item Deletion & Confirmation Modal
// -------------------------------------------------------------
let _pendingDeleteCartItemId = null;

function promptRemoveCartItem(itemId, productName = '') {
  if (!productName) {
    const row = document.getElementById(`cart-item-${itemId}`);
    if (row) {
      const link = row.querySelector('.cart-item-info a');
      if (link) productName = link.textContent.trim();
    }
  }

  const modal = document.getElementById('deleteConfirmModal');
  if (!modal) {
    const confirmMsg = productName
      ? `Are you sure you want to remove "${productName}" from your basket?`
      : 'Are you sure you want to remove this item from your basket?';
    if (confirm(confirmMsg)) {
      executeRemoveCartItem(itemId);
    }
    return;
  }

  _pendingDeleteCartItemId = itemId;
  const nameEl = document.getElementById('deleteModalItemName');
  if (nameEl) {
    nameEl.textContent = productName ? `"${productName}"` : 'this item';
  }

  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeDeleteModal() {
  const modal = document.getElementById('deleteConfirmModal');
  if (modal) {
    modal.style.display = 'none';
    document.body.style.overflow = '';
  }
  _pendingDeleteCartItemId = null;
}

function handleDeleteModalBackdrop(event) {
  if (event.target && event.target.id === 'deleteConfirmModal') {
    closeDeleteModal();
  }
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    const modal = document.getElementById('deleteConfirmModal');
    if (modal && modal.style.display !== 'none') {
      closeDeleteModal();
    }
  }
});

async function confirmDeleteCartItemAction() {
  if (!_pendingDeleteCartItemId) return;
  const itemId = _pendingDeleteCartItemId;
  const btn = document.getElementById('btnConfirmDeleteItem');
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>Removing...</span>';
  }

  await executeRemoveCartItem(itemId, () => {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
    closeDeleteModal();
  });
}

// Remove from Cart
async function removeCartItem(itemId, productName = '') {
  promptRemoveCartItem(itemId, productName);
}

async function executeRemoveCartItem(itemId, onComplete) {
  try {
    const response = await fetch(`/cart/remove/${itemId}/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      }
    });

    let data;
    try {
      data = await response.json();
    } catch (parseErr) {
      data = { success: false, error: 'Unexpected server response' };
    }

    if (response.ok && data.success) {
      const row = document.getElementById(`cart-item-${itemId}`);
      if (row) {
        row.style.transition = 'all 0.25s ease';
        row.style.opacity = '0';
        row.style.transform = 'translateX(-20px)';
        setTimeout(() => row.remove(), 240);
      }
      if (typeof window.applyCartDataToCartPage === 'function') {
        window.applyCartDataToCartPage(data);
      } else {
        updateCartUI(data);
      }
      if (typeof onComplete === 'function') onComplete();
      showToast(data.message || 'Item removed from basket', 'info');
    } else {
      if (typeof onComplete === 'function') onComplete();
      showToast(data.error || 'Could not remove item', 'danger');
    }
  } catch (err) {
    console.error('Remove cart error:', err);
    if (typeof onComplete === 'function') onComplete();
    showToast('Network error while removing item', 'danger');
  }
}

// Update Cart Quantity
async function updateCartItem(itemId, newQty) {
  if (newQty < 1) {
    return promptRemoveCartItem(itemId);
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

    let data;
    try {
      data = await response.json();
    } catch (parseErr) {
      data = { success: false, error: 'Unexpected server response' };
    }

    if (response.ok && data.success) {
      const row = document.getElementById(`cart-item-${itemId}`);
      if (row) {
        const qtyInput = row.querySelector('.qty-input');
        if (qtyInput) qtyInput.value = data.quantity;
        const totalCol = row.querySelector('.cart-item-total-col');
        if (totalCol && data.line_total) {
          totalCol.innerHTML = `<span class="cart-item-total-label" style="display: none; font-size: 0.8rem; color: var(--text-muted); font-weight: 500;">Subtotal: </span> ${data.line_total}`;
        }
      }
      if (typeof window.applyCartDataToCartPage === 'function') {
        window.applyCartDataToCartPage(data);
      } else {
        updateCartUI(data);
      }
    } else {
      showToast(data.error || 'Could not update quantity', 'danger');
    }
  } catch (err) {
    console.error('Update cart error:', err);
    showToast('Failed to update basket quantity', 'danger');
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
