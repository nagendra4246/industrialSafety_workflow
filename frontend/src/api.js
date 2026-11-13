const API_PREFIX = "/api/v1";

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const raw = await response.text();
  let payload = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = raw;
    }
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("; ")
      : detail || response.statusText || "Request failed";
    throw new Error(`${response.status}: ${message}`);
  }
  return payload;
}

export function apiGet(path, { signal } = {}) {
  return requestJson(`${API_PREFIX}${path}`, { signal });
}

export function apiPost(path, body) {
  return requestJson(`${API_PREFIX}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function apiPatch(path, body) {
  return requestJson(`${API_PREFIX}${path}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function queryString(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function fetchNewestPage(path, params, page, limit, signal) {
  const count = await apiGet(
    `${path}${queryString({ ...params, limit: 1, offset: 0 })}`,
    { signal },
  );
  const offset = Math.max(0, count.total - (page + 1) * limit);
  const result = await apiGet(
    `${path}${queryString({ ...params, limit, offset })}`,
    { signal },
  );
  return { ...result, items: result.items.slice().reverse() };
}

export async function fetchLatestPage(path, params, limit, signal) {
  return fetchNewestPage(path, params, 0, limit, signal);
}

export async function fetchAll(path, signal) {
  const first = await apiGet(`${path}${queryString({ limit: 200, offset: 0 })}`, {
    signal,
  });
  if (first.total <= first.items.length) {
    return first.items;
  }
  const pages = [];
  for (let offset = first.items.length; offset < first.total; offset += 200) {
    pages.push(
      apiGet(`${path}${queryString({ limit: 200, offset })}`, { signal }),
    );
  }
  const rest = await Promise.all(pages);
  return first.items.concat(...rest.map((page) => page.items));
}

export { requestJson };
