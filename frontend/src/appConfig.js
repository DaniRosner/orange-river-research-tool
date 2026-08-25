// Build-time naming config — same convention VITE_API_BASE_URL already
// uses (see .env.example). Generic fallback defaults so a fresh clone
// renders sensibly with no configuration; set the real values via
// VITE_PRODUCT_NAME / VITE_CLIENT_DISPLAY_NAME in the actual deploy
// (Railway frontend service variables), mirroring the backend's
// settings.product_name / settings.client_display_name.
export const PRODUCT_NAME = import.meta.env.VITE_PRODUCT_NAME || 'Research Tool'
export const CLIENT_DISPLAY_NAME = import.meta.env.VITE_CLIENT_DISPLAY_NAME || 'Your Firm'
