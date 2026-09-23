<template>
  <div class="min-h-screen bg-ink text-white">
    <div class="mx-auto max-w-7xl px-6 py-8 lg:px-10">
      <header class="panel mb-8 overflow-hidden">
        <div class="flex flex-col gap-4 border-b border-white/10 px-6 py-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div>
            <p class="text-xs uppercase tracking-[0.24em] text-white/35">Admin Studio</p>
            <h1 class="mt-2 text-2xl font-semibold">Live Commerce Control</h1>
          </div>
          <button class="button-primary" type="button" @click="downloadExcelExport">
            <ArrowDownTrayIcon class="mr-2 h-4 w-4" />
            Export Excel
          </button>
        </div>
        <div class="grid gap-4 px-6 py-6 lg:grid-cols-5 lg:px-8">
          <div class="rounded-[1.6rem] border border-white/10 bg-white/[0.03] p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-white/35">Profiles</p>
            <p class="mt-3 text-3xl font-semibold">{{ profiles.length }}</p>
          </div>
          <div class="rounded-[1.6rem] border border-white/10 bg-white/[0.03] p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-white/35">Products</p>
            <p class="mt-3 text-3xl font-semibold">{{ products.length }}</p>
          </div>
          <div class="rounded-[1.6rem] border border-white/10 bg-white/[0.03] p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-white/35">Orders</p>
            <p class="mt-3 text-3xl font-semibold">{{ orders.length }}</p>
          </div>
          <div class="rounded-[1.6rem] border border-white/10 bg-white/[0.03] p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-white/35">Cart</p>
            <p class="mt-3 text-3xl font-semibold">{{ cartCount }}</p>
          </div>
          <div class="rounded-[1.6rem] border border-white/10 bg-white/[0.03] p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-white/35">Inventory Value</p>
            <p class="mt-3 text-3xl font-semibold">{{ money(inventoryValue) }}</p>
          </div>
        </div>
      </header>

      <main v-if="ready && !errorMessage" class="space-y-8">
        <section class="grid gap-6 lg:grid-cols-[1.15fr_0.9fr_0.95fr]">
          <article class="panel p-6">
            <div class="panel-header">
              <div>
                <p class="text-xs uppercase tracking-[0.22em] text-white/40">Banner Listing</p>
                <h2 class="mt-2 text-2xl font-semibold">Featured products drive the storefront.</h2>
              </div>
              <MegaphoneIcon class="h-6 w-6 text-white/55" />
            </div>

            <div class="grid gap-4">
              <div
                v-for="product in featuredProducts"
                :key="product.id"
                class="rounded-[1.8rem] border border-white/10 bg-black/20 p-5"
              >
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <p class="text-lg font-medium">{{ product.name }}</p>
                    <p class="mt-1 text-sm text-white/50">{{ product.description }}</p>
                  </div>
                  <span class="chip">{{ product.category }}</span>
                </div>

                <div class="mt-5 flex items-center justify-between">
                  <div>
                    <p class="text-xs uppercase tracking-[0.2em] text-white/35">Price</p>
                    <p class="mt-1 text-xl font-semibold">{{ money(product.price) }}</p>
                  </div>
                  <button class="button-primary" @click="addBannerToCart(product)">
                    <ShoppingBagIcon class="mr-2 h-4 w-4" />
                    Add to Checkout
                  </button>
                </div>
              </div>
            </div>
          </article>

          <article class="panel p-6">
            <div class="panel-header">
              <div>
                <p class="text-xs uppercase tracking-[0.22em] text-white/40">Checkout</p>
                <h2 class="mt-2 text-2xl font-semibold">Build a cart, then create an order.</h2>
              </div>
              <CreditCardIcon class="h-6 w-6 text-white/55" />
            </div>

            <label class="label">Assign User</label>
            <select v-model="selectedProfileId" class="field" @change="onProfileChange">
              <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
                {{ profile.name }} · {{ profile.role }}
              </option>
            </select>

            <div class="mt-5 space-y-3">
              <div
                v-for="item in cart"
                :key="item.productId"
                class="rounded-[1.5rem] border border-white/10 bg-white/[0.025] p-4"
              >
                <div class="flex items-start justify-between gap-4">
                  <div>
                    <p class="font-medium">{{ item.name }}</p>
                    <p class="mt-1 text-sm text-white/45">{{ money(item.price) }} each</p>
                  </div>
                  <button class="text-sm text-white/45 transition hover:text-white" @click="removeCartItem(item.productId)">
                    Remove
                  </button>
                </div>

                <div class="mt-3 flex items-center gap-3">
                  <input
                    :value="item.quantity"
                    class="field max-w-[110px]"
                    min="1"
                    type="number"
                    @input="updateCart(item.productId, $event)"
                  />
                  <span class="text-sm text-white/45">Subtotal {{ money(item.quantity * item.price) }}</span>
                </div>
              </div>

              <div
                v-if="!cart.length"
                class="rounded-[1.5rem] border border-dashed border-white/10 px-4 py-8 text-center text-sm text-white/40"
              >
                Choose products from Banner Listing or Product CRUD to begin checkout.
              </div>
            </div>

            <label class="label mt-5">Checkout Notes</label>
            <textarea v-model="checkoutNotes" class="field min-h-[100px]" placeholder="Packaging requests, delivery notes, invoice memo"></textarea>

            <div class="mt-5 flex items-center justify-between border-t border-white/10 pt-5">
              <div>
                <p class="text-xs uppercase tracking-[0.2em] text-white/35">Total</p>
                <p class="mt-1 text-2xl font-semibold">{{ money(cartTotal) }}</p>
              </div>
              <button class="button-primary" @click="submitCheckout">
                Complete Checkout
              </button>
            </div>
          </article>

          <article class="panel p-6">
            <div class="panel-header">
              <div>
                <p class="text-xs uppercase tracking-[0.22em] text-white/40">User</p>
                <h2 class="mt-2 text-2xl font-semibold">Selected profile for order flow.</h2>
              </div>
              <UserCircleIcon class="h-6 w-6 text-white/55" />
            </div>

            <div v-if="activeProfile" class="space-y-4">
              <div class="rounded-[1.8rem] border border-white/10 bg-white/[0.03] p-5">
                <p class="text-xs uppercase tracking-[0.2em] text-white/35">{{ activeProfile.role }}</p>
                <p class="mt-2 text-2xl font-semibold">{{ activeProfile.name }}</p>
                <p class="mt-2 text-sm text-white/50">{{ activeProfile.email }}</p>
                <p class="mt-1 text-sm text-white/50">{{ activeProfile.phone }}</p>
                <p class="mt-4 text-sm leading-6 text-white/60">{{ activeProfile.address }}</p>
              </div>

              <form class="space-y-3" @submit.prevent="submitProfile">
                <div class="grid gap-3">
                  <div>
                    <label class="label">Name</label>
                    <input v-model="profileForm.name" class="field" type="text" />
                  </div>
                  <div>
                    <label class="label">Email</label>
                    <input v-model="profileForm.email" class="field" type="email" />
                  </div>
                  <div class="grid gap-3 md:grid-cols-2">
                    <div>
                      <label class="label">Role</label>
                      <input v-model="profileForm.role" class="field" type="text" />
                    </div>
                    <div>
                      <label class="label">Phone</label>
                      <input v-model="profileForm.phone" class="field" type="text" />
                    </div>
                  </div>
                  <div>
                    <label class="label">Address</label>
                    <textarea v-model="profileForm.address" class="field min-h-[84px]"></textarea>
                  </div>
                </div>

                <div class="flex flex-wrap gap-3">
                  <button class="button-primary" type="submit">{{ profileForm.id ? "Update Profile" : "Create Profile" }}</button>
                  <button class="button-secondary" type="button" @click="resetProfileForm">Reset</button>
                  <button
                    v-if="profileForm.id"
                    class="button-danger"
                    type="button"
                    @click="removeProfile(profileForm.id)"
                  >
                    Delete
                  </button>
                </div>
              </form>
            </div>
          </article>
        </section>

        <section class="grid gap-6 lg:grid-cols-[1fr_1fr]">
          <article class="panel p-6">
            <div class="panel-header">
              <div>
                <p class="text-xs uppercase tracking-[0.22em] text-white/40">Warehouse</p>
                <h2 class="mt-2 text-2xl font-semibold">Inventory reacts directly to checkout and CRUD.</h2>
              </div>
              <ArchiveBoxIcon class="h-6 w-6 text-white/55" />
            </div>

            <div class="space-y-3">
              <div
                v-for="product in products"
                :key="product.id"
                class="flex items-center justify-between rounded-[1.4rem] border border-white/10 bg-white/[0.025] px-4 py-4"
              >
                <div>
                  <p class="font-medium">{{ product.name }}</p>
                  <p class="mt-1 text-sm text-white/45">{{ product.sku }} · {{ product.category }}</p>
                </div>
                <div class="text-right">
                  <p class="text-lg font-semibold">{{ product.stock }} units</p>
                  <p
                    class="mt-1 text-xs uppercase tracking-[0.16em]"
                    :class="Number(product.stock) <= 5 ? 'text-amber-300' : 'text-white/35'"
                  >
                    {{ Number(product.stock) <= 5 ? "Low stock" : "Healthy stock" }}
                  </p>
                </div>
              </div>
            </div>
          </article>

          <article class="panel p-6">
            <div class="panel-header">
              <div>
                <p class="text-xs uppercase tracking-[0.22em] text-white/40">Order</p>
                <h2 class="mt-2 text-2xl font-semibold">Order queue linked to warehouse movement.</h2>
              </div>
              <ClipboardDocumentListIcon class="h-6 w-6 text-white/55" />
            </div>

            <div class="space-y-3">
              <div
                v-for="order in orders"
                :key="order.id"
                class="rounded-[1.5rem] border border-white/10 bg-white/[0.025] p-4"
              >
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <p class="text-sm uppercase tracking-[0.18em] text-white/35">Order #{{ order.id }}</p>
                    <p class="mt-2 text-lg font-medium">{{ order.user_name }}</p>
                  </div>
                  <span class="chip">{{ order.status }}</span>
                </div>
                <div class="mt-3 flex flex-wrap gap-2">
                  <span
                    v-for="item in order.items"
                    :key="`${order.id}-${item.productId}`"
                    class="rounded-full border border-white/10 px-3 py-1 text-xs text-white/55"
                  >
                    {{ item.name }} × {{ item.quantity }}
                  </span>
                </div>
                <div class="mt-4 flex items-center justify-between">
                  <p class="text-sm text-white/45">{{ order.notes || "No notes" }}</p>
                  <p class="text-lg font-semibold">{{ money(order.total) }}</p>
                </div>
              </div>
            </div>
          </article>
        </section>

        <section class="panel p-6">
          <div class="panel-header">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-white/40">Full CRUD</p>
              <h2 class="mt-2 text-2xl font-semibold">Profile, Order, and Product console.</h2>
            </div>
            <AdjustmentsHorizontalIcon class="h-6 w-6 text-white/55" />
          </div>

          <div class="mb-6 flex flex-wrap gap-3">
            <button
              v-for="tab in crudTabs"
              :key="tab"
              class="button-secondary"
              :class="{ '!border-white !bg-white !text-black': activeTab === tab }"
              @click="activeTab = tab"
            >
              {{ tab }}
            </button>
          </div>

          <div class="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
            <div v-if="activeTab === 'Product'" class="space-y-4">
              <form class="space-y-3" @submit.prevent="submitProduct">
                <div>
                  <label class="label">Product Name</label>
                  <input v-model="productForm.name" class="field" type="text" required />
                </div>
                <div class="grid gap-3 md:grid-cols-2">
                  <div>
                    <label class="label">SKU</label>
                    <input v-model="productForm.sku" class="field" type="text" required />
                  </div>
                  <div>
                    <label class="label">Category</label>
                    <input v-model="productForm.category" class="field" type="text" required />
                  </div>
                </div>
                <div class="grid gap-3 md:grid-cols-3">
                  <div>
                    <label class="label">Price</label>
                    <input v-model.number="productForm.price" class="field" type="number" min="0" step="0.01" required />
                  </div>
                  <div>
                    <label class="label">Stock</label>
                    <input v-model.number="productForm.stock" class="field" type="number" min="0" required />
                  </div>
                  <div>
                    <label class="label">Featured</label>
                    <select v-model.number="productForm.featured" class="field">
                      <option :value="1">Yes</option>
                      <option :value="0">No</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="label">Description</label>
                  <textarea v-model="productForm.description" class="field min-h-[110px]"></textarea>
                </div>
                <div class="flex flex-wrap gap-3">
                  <button class="button-primary" type="submit">{{ productForm.id ? "Update Product" : "Create Product" }}</button>
                  <button class="button-secondary" type="button" @click="resetProductForm">Reset</button>
                  <button v-if="productForm.id" class="button-danger" type="button" @click="removeProduct(productForm.id)">Delete</button>
                </div>
              </form>
            </div>

            <div v-else-if="activeTab === 'Order'" class="space-y-4">
              <form class="space-y-3" @submit.prevent="submitOrder">
                <div class="grid gap-3 md:grid-cols-2">
                  <div>
                    <label class="label">User Name</label>
                    <input v-model="orderForm.user_name" class="field" type="text" required />
                  </div>
                  <div>
                    <label class="label">Status</label>
                    <select v-model="orderForm.status" class="field">
                      <option>Pending</option>
                      <option>Packed</option>
                      <option>Shipped</option>
                      <option>Completed</option>
                    </select>
                  </div>
                </div>
                <div class="grid gap-3 md:grid-cols-2">
                  <div>
                    <label class="label">Profile</label>
                    <select v-model.number="orderForm.profile_id" class="field">
                      <option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
                    </select>
                  </div>
                  <div>
                    <label class="label">Total</label>
                    <input v-model.number="orderForm.total" class="field" type="number" min="0" step="0.01" />
                  </div>
                </div>
                <div>
                  <label class="label">Items JSON</label>
                  <textarea v-model="orderForm.items_json" class="field min-h-[140px]"></textarea>
                </div>
                <div>
                  <label class="label">Notes</label>
                  <textarea v-model="orderForm.notes" class="field min-h-[110px]"></textarea>
                </div>
                <div class="flex flex-wrap gap-3">
                  <button class="button-primary" type="submit">{{ orderForm.id ? "Update Order" : "Create Order" }}</button>
                  <button class="button-secondary" type="button" @click="resetOrderForm">Reset</button>
                  <button v-if="orderForm.id" class="button-danger" type="button" @click="removeOrder(orderForm.id)">Delete</button>
                </div>
              </form>
            </div>

            <div v-else class="space-y-4">
              <form class="space-y-3" @submit.prevent="submitProfile">
                <div>
                  <label class="label">Full Name</label>
                  <input v-model="profileForm.name" class="field" type="text" required />
                </div>
                <div class="grid gap-3 md:grid-cols-2">
                  <div>
                    <label class="label">Email</label>
                    <input v-model="profileForm.email" class="field" type="email" required />
                  </div>
                  <div>
                    <label class="label">Role</label>
                    <input v-model="profileForm.role" class="field" type="text" required />
                  </div>
                </div>
                <div>
                  <label class="label">Phone</label>
                  <input v-model="profileForm.phone" class="field" type="text" />
                </div>
                <div>
                  <label class="label">Address</label>
                  <textarea v-model="profileForm.address" class="field min-h-[110px]"></textarea>
                </div>
                <div class="flex flex-wrap gap-3">
                  <button class="button-primary" type="submit">{{ profileForm.id ? "Update Profile" : "Create Profile" }}</button>
                  <button class="button-secondary" type="button" @click="resetProfileForm">Reset</button>
                  <button v-if="profileForm.id" class="button-danger" type="button" @click="removeProfile(profileForm.id)">Delete</button>
                </div>
              </form>
            </div>

            <div class="rounded-[2rem] border border-white/10 bg-black/20 p-4">
              <div v-if="activeTab === 'Product'" class="space-y-3">
                <button
                  v-for="product in products"
                  :key="product.id"
                  class="flex w-full items-center justify-between rounded-[1.4rem] border border-white/10 bg-white/[0.025] px-4 py-4 text-left transition hover:border-white/25"
                  @click="editProduct(product)"
                >
                  <div>
                    <p class="font-medium">{{ product.name }}</p>
                    <p class="mt-1 text-sm text-white/45">{{ product.sku }} · {{ money(product.price) }}</p>
                  </div>
                  <ChevronRightIcon class="h-5 w-5 text-white/35" />
                </button>
              </div>

              <div v-else-if="activeTab === 'Order'" class="space-y-3">
                <button
                  v-for="order in orders"
                  :key="order.id"
                  class="flex w-full items-center justify-between rounded-[1.4rem] border border-white/10 bg-white/[0.025] px-4 py-4 text-left transition hover:border-white/25"
                  @click="editOrder(order)"
                >
                  <div>
                    <p class="font-medium">Order #{{ order.id }} · {{ order.user_name }}</p>
                    <p class="mt-1 text-sm text-white/45">{{ order.status }} · {{ money(order.total) }}</p>
                  </div>
                  <ChevronRightIcon class="h-5 w-5 text-white/35" />
                </button>
              </div>

              <div v-else class="space-y-3">
                <button
                  v-for="profile in profiles"
                  :key="profile.id"
                  class="flex w-full items-center justify-between rounded-[1.4rem] border border-white/10 bg-white/[0.025] px-4 py-4 text-left transition hover:border-white/25"
                  @click="editProfile(profile)"
                >
                  <div>
                    <p class="font-medium">{{ profile.name }}</p>
                    <p class="mt-1 text-sm text-white/45">{{ profile.role }} · {{ profile.email }}</p>
                  </div>
                  <ChevronRightIcon class="h-5 w-5 text-white/35" />
                </button>
              </div>
            </div>
          </div>
        </section>
      </main>

      <div v-else-if="errorMessage" class="panel p-12 text-center text-white/75">
        {{ errorMessage }}
      </div>
      <div v-else class="panel p-12 text-center text-white/60">Connecting to live database...</div>
    </div>
  </div>
</template>

<script>
import {
  AdjustmentsHorizontalIcon,
  ArchiveBoxIcon,
  ArrowDownTrayIcon,
  ChevronRightIcon,
  ClipboardDocumentListIcon,
  CreditCardIcon,
  MegaphoneIcon,
  ShoppingBagIcon,
  UserCircleIcon
} from "@heroicons/vue/24/outline";
import { useAppStore } from "./stores/app";

function productDefaults() {
  return {
    id: null,
    name: "",
    sku: "",
    category: "",
    price: 0,
    stock: 0,
    featured: 1,
    description: ""
  };
}

function profileDefaults() {
  return {
    id: null,
    name: "",
    email: "",
    role: "Buyer",
    phone: "",
    address: ""
  };
}

function orderDefaults(profileId = null, profileName = "") {
  return {
    id: null,
    profile_id: profileId,
    user_name: profileName,
    status: "Pending",
    total: 0,
    items_json: "[]",
    notes: ""
  };
}

export default {
  name: "App",
  components: {
    AdjustmentsHorizontalIcon,
    ArchiveBoxIcon,
    ArrowDownTrayIcon,
    ChevronRightIcon,
    ClipboardDocumentListIcon,
    CreditCardIcon,
    MegaphoneIcon,
    ShoppingBagIcon,
    UserCircleIcon
  },
  data() {
    return {
      store: null,
      checkoutNotes: "",
      selectedProfileId: null,
      activeTab: "Product",
      crudTabs: ["Product", "Order", "Profile"],
      productForm: productDefaults(),
      profileForm: profileDefaults(),
      orderForm: orderDefaults()
    };
  },
  computed: {
    ready() {
      return this.store?.ready || false;
    },
    profiles() {
      return this.store?.profiles || [];
    },
    products() {
      return this.store?.products || [];
    },
    orders() {
      return this.store?.orders || [];
    },
    cart() {
      return this.store?.cart || [];
    },
    featuredProducts() {
      return this.store?.featuredProducts || [];
    },
    cartCount() {
      return this.store?.cartCount || 0;
    },
    cartTotal() {
      return this.store?.cartTotal || 0;
    },
    inventoryValue() {
      return this.store?.inventoryValue || 0;
    },
    activeProfile() {
      return this.store?.activeProfile || null;
    },
    errorMessage() {
      return this.store?.error || "";
    }
  },
  async mounted() {
    this.store = useAppStore();
    await this.store.initialize();
    this.selectedProfileId = this.store.activeProfileId;
    this.hydrateForms();
  },
  methods: {
    money(value) {
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0
      }).format(Number(value || 0));
    },
    hydrateForms() {
      if (this.activeProfile) {
        this.profileForm = { ...this.activeProfile };
        this.orderForm = orderDefaults(this.activeProfile.id, this.activeProfile.name);
      } else {
        this.profileForm = profileDefaults();
      }
      this.productForm = productDefaults();
      this.selectedProfileId = this.store.activeProfileId;
    },
    onProfileChange() {
      this.store.selectProfile(this.selectedProfileId);
      this.profileForm = this.activeProfile ? { ...this.activeProfile } : profileDefaults();
      this.orderForm.profile_id = this.activeProfile?.id || null;
      this.orderForm.user_name = this.activeProfile?.name || "";
    },
    addBannerToCart(product) {
      this.store.addToCart(product);
    },
    updateCart(productId, event) {
      this.store.updateCartQuantity(productId, event.target.value);
    },
    removeCartItem(productId) {
      this.store.removeFromCart(productId);
    },
    async submitCheckout() {
      const success = await this.store.checkout(this.checkoutNotes);
      if (success) {
        this.checkoutNotes = "";
      }
    },
    async submitProduct() {
      await this.store.saveProduct({
        ...this.productForm,
        price: Number(this.productForm.price),
        stock: Number(this.productForm.stock),
        featured: Number(this.productForm.featured)
      });
      this.resetProductForm();
    },
    resetProductForm() {
      this.productForm = productDefaults();
    },
    editProduct(product) {
      this.productForm = {
        ...product,
        featured: Number(product.featured)
      };
    },
    async removeProduct(id) {
      await this.store.deleteProduct(id);
      this.resetProductForm();
    },
    async submitProfile() {
      await this.store.saveProfile({ ...this.profileForm });
      this.resetProfileForm();
      this.selectedProfileId = this.store.activeProfileId;
    },
    resetProfileForm() {
      this.profileForm = this.activeProfile ? { ...this.activeProfile } : profileDefaults();
    },
    editProfile(profile) {
      this.profileForm = { ...profile };
      this.activeTab = "Profile";
    },
    async removeProfile(id) {
      await this.store.deleteProfile(id);
      this.resetProfileForm();
      this.selectedProfileId = this.store.activeProfileId;
    },
    async submitOrder() {
      await this.store.saveOrder({
        ...this.orderForm,
        total: Number(this.orderForm.total),
        items: JSON.parse(this.orderForm.items_json || "[]")
      });
      this.resetOrderForm();
    },
    resetOrderForm() {
      this.orderForm = orderDefaults(this.activeProfile?.id || null, this.activeProfile?.name || "");
    },
    editOrder(order) {
      this.orderForm = {
        id: order.id,
        profile_id: order.profile_id,
        user_name: order.user_name,
        status: order.status,
        total: Number(order.total),
        items_json: JSON.stringify(order.items, null, 2),
        notes: order.notes || ""
      };
      this.activeTab = "Order";
    },
    async removeOrder(id) {
      await this.store.deleteOrder(id);
      this.resetOrderForm();
    },
    async downloadExcelExport() {
      try {
        const { blob, filename } = await this.store.exportExcel();
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
      } catch (error) {
        this.store.error = error.message || "Unable to export Excel.";
      }
    }
  },
  watch: {
    activeProfile(nextProfile) {
      if (!this.profileForm.id && nextProfile) {
        this.profileForm = { ...nextProfile };
      }
    }
  }
};
</script>
