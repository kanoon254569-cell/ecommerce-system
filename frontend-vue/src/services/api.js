const API_BASE = window.location.origin;

function readJsonSafely(text) {
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function getAuthToken() {
  const token = localStorage.getItem("auth_token");
  if (!token) {
    throw new Error("Please log in before opening Admin.");
  }
  return token;
}

async function request(path, options = {}) {
  const token = getAuthToken();
  const headers = {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {})
  };

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined
  });

  const raw = await response.text();
  const data = readJsonSafely(raw);

  if (!response.ok) {
    const detail = data?.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg || "Validation error").join(", "));
    }
    if (typeof detail === "string") {
      throw new Error(detail);
    }
    if (typeof data === "string" && data.trim()) {
      throw new Error(data);
    }
    throw new Error(`Request failed with status ${response.status}`);
  }

  return data;
}

async function download(path) {
  const token = getAuthToken();
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });

  if (!response.ok) {
    const raw = await response.text();
    const data = readJsonSafely(raw);
    throw new Error(data?.detail || raw || `Request failed with status ${response.status}`);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  return {
    blob,
    filename: filenameMatch ? filenameMatch[1] : "export_all_data.xlsx"
  };
}

export const studioApi = {
  bootstrap() {
    return request("/api/admin/studio/bootstrap");
  },
  createProfile(payload) {
    return request("/api/admin/studio/profiles", { method: "POST", body: payload });
  },
  updateProfile(id, payload) {
    return request(`/api/admin/studio/profiles/${id}`, { method: "PUT", body: payload });
  },
  deleteProfile(id) {
    return request(`/api/admin/studio/profiles/${id}`, { method: "DELETE" });
  },
  createProduct(payload) {
    return request("/api/admin/studio/products", { method: "POST", body: payload });
  },
  updateProduct(id, payload) {
    return request(`/api/admin/studio/products/${id}`, { method: "PUT", body: payload });
  },
  deleteProduct(id) {
    return request(`/api/admin/studio/products/${id}`, { method: "DELETE" });
  },
  createOrder(payload) {
    return request("/api/admin/studio/orders", { method: "POST", body: payload });
  },
  updateOrder(id, payload) {
    return request(`/api/admin/studio/orders/${id}`, { method: "PUT", body: payload });
  },
  deleteOrder(id) {
    return request(`/api/admin/studio/orders/${id}`, { method: "DELETE" });
  },
  checkout(payload) {
    return request("/api/admin/studio/checkout", { method: "POST", body: payload });
  },
  exportExcel() {
    return download("/api/admin/export-excel");
  }
};
