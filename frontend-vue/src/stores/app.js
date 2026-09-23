import { defineStore } from "pinia";
import { studioApi } from "../services/api";

function normalizeId(value) {
  return value == null ? null : String(value);
}

export const useAppStore = defineStore("app", {
  state: () => ({
    ready: false,
    error: "",
    profiles: [],
    products: [],
    orders: [],
    cart: [],
    activeProfileId: null
  }),
  getters: {
    featuredProducts(state) {
      return state.products.filter((product) => Number(product.featured) === 1);
    },
    activeProfile(state) {
      return (
        state.profiles.find((profile) => profile.id === state.activeProfileId) ||
        state.profiles[0] ||
        null
      );
    },
    cartCount(state) {
      return state.cart.reduce((total, item) => total + item.quantity, 0);
    },
    cartTotal(state) {
      return state.cart.reduce((total, item) => total + item.quantity * item.price, 0);
    },
    lowStockProducts(state) {
      return state.products.filter((product) => Number(product.stock) <= 5);
    },
    inventoryValue(state) {
      return state.products.reduce(
        (total, product) => total + Number(product.price) * Number(product.stock),
        0
      );
    }
  },
  actions: {
    applySnapshot(snapshot) {
      this.profiles = snapshot.profiles || [];
      this.products = snapshot.products || [];
      this.orders = snapshot.orders || [];

      const validProfileId = this.profiles.some((profile) => profile.id === this.activeProfileId);
      this.activeProfileId = validProfileId
        ? this.activeProfileId
        : this.profiles[0]?.id || null;

      this.cart = this.cart.filter((cartItem) => {
        const product = this.products.find((productEntry) => productEntry.id === cartItem.productId);
        return product && Number(product.stock) > 0;
      }).map((cartItem) => {
        const product = this.products.find((productEntry) => productEntry.id === cartItem.productId);
        return {
          ...cartItem,
          quantity: Math.min(cartItem.quantity, Number(product?.stock || 0))
        };
      });
    },
    async initialize() {
      this.error = "";
      try {
        const snapshot = await studioApi.bootstrap();
        this.applySnapshot(snapshot);
      } catch (error) {
        this.error = error.message || "Unable to connect to the live database.";
      } finally {
        this.ready = true;
      }
    },
    async reloadAll() {
      const snapshot = await studioApi.bootstrap();
      this.applySnapshot(snapshot);
    },
    selectProfile(id) {
      this.activeProfileId = normalizeId(id);
    },
    async saveProfile(payload) {
      this.error = "";
      const requestPayload = {
        name: payload.name,
        email: payload.email,
        role: String(payload.role || "user").toLowerCase(),
        phone: payload.phone || "",
        address: payload.address || ""
      };

      try {
        const response = payload.id
          ? await studioApi.updateProfile(payload.id, requestPayload)
          : await studioApi.createProfile(requestPayload);

        await this.reloadAll();
        this.activeProfileId = response.profile?.id || this.activeProfileId;
      } catch (error) {
        this.error = error.message || "Unable to save profile.";
        throw error;
      }
    },
    async deleteProfile(id) {
      this.error = "";
      try {
        await studioApi.deleteProfile(id);
        if (this.activeProfileId === id) {
          this.activeProfileId = null;
        }
        await this.reloadAll();
      } catch (error) {
        this.error = error.message || "Unable to delete profile.";
        throw error;
      }
    },
    async saveProduct(payload) {
      this.error = "";
      const requestPayload = {
        name: payload.name,
        sku: payload.sku,
        category: payload.category,
        price: Number(payload.price),
        stock: Number(payload.stock),
        featured: Number(payload.featured),
        description: payload.description || ""
      };

      try {
        if (payload.id) {
          await studioApi.updateProduct(payload.id, requestPayload);
        } else {
          await studioApi.createProduct(requestPayload);
        }
        await this.reloadAll();
      } catch (error) {
        this.error = error.message || "Unable to save product.";
        throw error;
      }
    },
    async deleteProduct(id) {
      this.error = "";
      try {
        await studioApi.deleteProduct(id);
        this.cart = this.cart.filter((item) => item.productId !== id);
        await this.reloadAll();
      } catch (error) {
        this.error = error.message || "Unable to delete product.";
        throw error;
      }
    },
    async saveOrder(payload) {
      this.error = "";
      const requestPayload = {
        profile_id: normalizeId(payload.profile_id),
        user_name: payload.user_name,
        status: payload.status,
        total: Number(payload.total),
        items: payload.items || [],
        notes: payload.notes || ""
      };

      try {
        if (payload.id) {
          await studioApi.updateOrder(payload.id, requestPayload);
        } else {
          await studioApi.createOrder(requestPayload);
        }
        await this.reloadAll();
      } catch (error) {
        this.error = error.message || "Unable to save order.";
        throw error;
      }
    },
    async deleteOrder(id) {
      this.error = "";
      try {
        await studioApi.deleteOrder(id);
        await this.reloadAll();
      } catch (error) {
        this.error = error.message || "Unable to delete order.";
        throw error;
      }
    },
    addToCart(product) {
      if (Number(product.stock) <= 0) {
        return;
      }

      const existing = this.cart.find((item) => item.productId === product.id);
      if (existing) {
        existing.quantity = Math.min(existing.quantity + 1, Number(product.stock));
      } else {
        this.cart.push({
          productId: product.id,
          name: product.name,
          price: Number(product.price),
          quantity: 1
        });
      }
    },
    updateCartQuantity(productId, quantity) {
      const target = this.cart.find((item) => item.productId === productId);
      const product = this.products.find((item) => item.id === productId);
      if (!target || !product) {
        return;
      }

      const nextQuantity = Number(quantity);
      if (nextQuantity <= 0) {
        this.removeFromCart(productId);
        return;
      }

      target.quantity = Math.min(nextQuantity, Number(product.stock));
    },
    removeFromCart(productId) {
      this.cart = this.cart.filter((item) => item.productId !== productId);
    },
    async checkout(notes = "") {
      if (!this.cart.length || !this.activeProfile) {
        return false;
      }

      this.error = "";
      try {
        await studioApi.checkout({
          profile_id: this.activeProfile.id,
          items: this.cart.map((item) => ({
            productId: item.productId,
            name: item.name,
            quantity: Number(item.quantity),
            price: Number(item.price)
          })),
          notes
        });

        this.cart = [];
        await this.reloadAll();
        return true;
      } catch (error) {
        this.error = error.message || "Unable to complete checkout.";
        throw error;
      }
    },
    async exportExcel() {
      this.error = "";
      try {
        return await studioApi.exportExcel();
      } catch (error) {
        this.error = error.message || "Unable to export Excel.";
        throw error;
      }
    }
  }
});
